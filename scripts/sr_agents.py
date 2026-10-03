#!/usr/bin/env python3
"""Deterministic, auditable runtime for the sr multi-agent research workflow."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROLE_IDS = ("coordinator", "evidence", "simulation", "baseline", "red_team")
RESULT_STATUSES = {"pending", "completed", "blocked", "failed"}
CLAIM_STATUSES = {"supported", "inferred", "unresolved"}
ROLE_DEPENDENCIES = {
    "coordinator": ("evidence", "simulation", "baseline", "red_team"),
    "evidence": (),
    "simulation": (),
    "baseline": ("simulation",),
    "red_team": ("evidence", "simulation", "baseline"),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_path(name: str) -> Path:
    if not name or Path(name).name != name or name.startswith("_"):
        raise ValueError("invalid project name")
    path = ROOT / "projects" / name
    if not path.is_dir():
        raise ValueError(f"project not found: {name}")
    return path


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON object required: {path}")
    return data


def runs_dir(project: str) -> Path:
    path = project_path(project) / "agents" / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_dir(project: str, run_id: str) -> Path:
    path = runs_dir(project) / run_id
    if not path.is_dir():
        raise ValueError(f"run not found: {project}/{run_id}")
    return path


def init_project(args: argparse.Namespace) -> None:
    project = project_path(args.project)
    base = project / "agents"
    (base / "runs").mkdir(parents=True, exist_ok=True)
    readme = base / "README.md"
    if not readme.exists():
        readme.write_text(
            "# 多智能体运行资产\n\n"
            "运行由 `python3 scripts/sr.py agents plan ...` 创建；每个运行保留 manifest、角色结果、日志和报告。\n",
            encoding="utf-8",
        )
    print(base.relative_to(ROOT))


def make_result(role: str, run_id: str, status: str, summary: str, **extra) -> dict:
    if status not in RESULT_STATUSES:
        raise ValueError(f"invalid result status: {status}")
    result = {
        "schema_version": "0.1.0",
        "run_id": run_id,
        "role": role,
        "status": status,
        "created_at": now(),
        "summary": summary,
        "claims": [],
        "evidence": [],
        "metrics": [],
        "artifacts": [],
        "issues": [],
        "next_actions": [],
    }
    result.update(extra)
    return result


def plan(args: argparse.Namespace) -> None:
    project_path(args.project)
    run_id = args.run or datetime.now().strftime("%Y%m%d-%H%M%S")
    if not run_id.replace("-", "").isalnum():
        raise ValueError("invalid run id")
    base = runs_dir(args.project) / run_id
    if base.exists():
        raise ValueError(f"run already exists: {run_id}")
    (base / "tasks").mkdir(parents=True)
    (base / "results").mkdir()
    (base / "artifacts").mkdir()
    roles = []
    for role in ROLE_IDS:
        roles.append({"id": role, "status": "pending", "task": f"tasks/{role}.md", "result": f"results/{role}.json"})
        (base / "tasks" / f"{role}.md").write_text(
            f"# {role} task\n\n研究问题：{args.question}\n\n请严格按照 `agents/roles/{role}.md` 的输入、输出和证据要求工作。\n",
            encoding="utf-8",
        )
        write_json(base / "results" / f"{role}.json", make_result(role, run_id, "pending", "任务尚未执行"))
    manifest = {
        "schema_version": "0.1.0",
        "project": args.project,
        "run_id": run_id,
        "question": args.question,
        "created_at": now(),
        "status": "planned",
        "roles": roles,
        "inputs": {"paper_ir": None, "simulation_command": None, "metrics": None},
    }
    write_json(base / "manifest.json", manifest)
    print(run_id)


def update_manifest(base: Path, **changes) -> dict:
    manifest = read_json(base / "manifest.json")
    manifest.update(changes)
    write_json(base / "manifest.json", manifest)
    return manifest


def evidence_agent(base: Path, manifest: dict, paper_ir: Path | None) -> dict:
    run_id = manifest["run_id"]
    if paper_ir is None:
        return make_result("evidence", run_id, "blocked", "未提供 PaperIR 文献资产", issues=[{"severity": "high", "text": "需要 --paper-ir"}])
    try:
        from importlib.util import module_from_spec, spec_from_file_location
        spec = spec_from_file_location("paper_ir", ROOT / "projects" / "paper-ir" / "paper_ir.py")
        module = module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        artifact = module.load_json(paper_ir)
        errors = module.validate_artifact(artifact, paper_ir)
    except Exception as exc:
        return make_result("evidence", run_id, "failed", "PaperIR 读取失败", issues=[{"severity": "high", "text": str(exc)}])
    if errors:
        return make_result("evidence", run_id, "failed", "PaperIR 校验失败", issues=[{"severity": "high", "text": error} for error in errors])
    claims = []
    evidence = []
    unresolved = 0
    for item in artifact["items"]:
        claim = {"item_id": item["id"], "kind": item["kind"], "status": item["status"], "text": item["text"]}
        claims.append(claim)
        if item["status"] == "unresolved":
            unresolved += 1
        for ref in item["evidence"]:
            evidence.append({"item_id": item["id"], **ref})
    status = "completed"
    summary = f"读取 {len(claims)} 条语义条目和 {len(evidence)} 条原文证据"
    return make_result("evidence", run_id, status, summary, claims=claims, evidence=evidence,
                       artifacts=[{"path": str(paper_ir), "type": "paper-ir"}],
                       issues=([{"severity": "medium", "text": f"存在 {unresolved} 条 unresolved 条目"}] if unresolved else []),
                       next_actions=["人工核对 supported 条目的概括是否被引文真正支持"])


def simulation_agent(base: Path, manifest: dict, command: str | None) -> dict:
    run_id = manifest["run_id"]
    if not command:
        return make_result("simulation", run_id, "blocked", "未提供仿真命令", issues=[{"severity": "high", "text": "需要 --simulation-command"}])
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return make_result("simulation", run_id, "failed", "仿真命令解析失败", issues=[{"severity": "high", "text": str(exc)}])
    if not argv:
        return make_result("simulation", run_id, "failed", "仿真命令为空")
    started = time.time()
    try:
        proc = subprocess.run(argv, cwd=project_path(manifest["project"]), capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as exc:
        return make_result("simulation", run_id, "failed", "仿真超时", issues=[{"severity": "high", "text": "超过 120 秒"}], artifacts=[])
    stdout = base / "artifacts" / "simulation.stdout.txt"
    stderr = base / "artifacts" / "simulation.stderr.txt"
    stdout.write_text(proc.stdout, encoding="utf-8")
    stderr.write_text(proc.stderr, encoding="utf-8")
    status = "completed" if proc.returncode == 0 else "failed"
    return make_result("simulation", run_id, status,
                       f"仿真返回码 {proc.returncode}，耗时 {time.time() - started:.2f} 秒",
                       artifacts=[{"path": str(stdout.relative_to(base)), "type": "stdout"}, {"path": str(stderr.relative_to(base)), "type": "stderr"}],
                       metrics=[{"name": "return_code", "value": proc.returncode}],
                       issues=[] if proc.returncode == 0 else [{"severity": "high", "text": proc.stderr[-2000:]}],
                       next_actions=[] if proc.returncode == 0 else ["修复仿真错误后重新运行"])


def baseline_agent(base: Path, manifest: dict, metrics_path: Path | None) -> dict:
    run_id = manifest["run_id"]
    if metrics_path is None:
        return make_result("baseline", run_id, "blocked", "未提供指标文件", issues=[{"severity": "high", "text": "需要 --metrics"}])
    try:
        data = read_json(metrics_path)
        metrics = data.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise ValueError("metrics 必须是非空数组")
        checked = []
        issues = []
        for metric in metrics:
            name = metric.get("name")
            value = metric.get("value")
            baseline = metric.get("baseline")
            higher = metric.get("higher_is_better")
            if not isinstance(name, str) or not isinstance(value, (int, float)) or not isinstance(baseline, (int, float)) or not isinstance(higher, bool):
                issues.append({"severity": "high", "text": f"指标字段不完整: {metric}"})
                continue
            delta = value - baseline
            passed = delta >= 0 if higher else delta <= 0
            checked.append({"name": name, "value": value, "baseline": baseline, "delta": delta, "passed": passed})
            if not passed:
                issues.append({"severity": "medium", "text": f"{name} 未超过基线"})
        status = "completed" if checked and not any(i["severity"] == "high" for i in issues) else "failed"
        return make_result("baseline", run_id, status, f"检查 {len(checked)} 项指标", metrics=checked, issues=issues,
                           artifacts=[{"path": str(metrics_path), "type": "metrics"}],
                           next_actions=["补充相同数据划分、训练预算和硬件条件"] if checked else [])
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return make_result("baseline", run_id, "failed", "指标读取失败", issues=[{"severity": "high", "text": str(exc)}])


def red_team_agent(base: Path, manifest: dict, results: dict[str, dict]) -> dict:
    issues = []
    evidence = results["evidence"]
    simulation = results["simulation"]
    baseline = results["baseline"]
    for claim in evidence.get("claims", []):
        if claim.get("status") == "supported" and not any(ref.get("item_id") == claim.get("item_id") for ref in evidence.get("evidence", [])):
            issues.append({"severity": "high", "text": f"supported 条目缺少证据: {claim.get('item_id')}"})
    if simulation.get("status") != "completed":
        issues.append({"severity": "high", "text": "仿真未成功完成，不能声称复现"})
    if baseline.get("status") != "completed":
        issues.append({"severity": "high", "text": "经典基线审查未完成，不能声称优势"})
    if not evidence.get("claims"):
        issues.append({"severity": "medium", "text": "没有可追溯的论文主张"})
    if not issues:
        return make_result("red_team", manifest["run_id"], "completed", "基础证据、仿真和基线检查通过；仍需人工检查数据泄漏和物理假设", next_actions=["人工复核训练/测试划分、损耗模型和资源口径"])
    return make_result("red_team", manifest["run_id"], "completed", f"发现 {len(issues)} 个需要处理的问题", issues=issues,
                       next_actions=["在报告中保留这些问题，修复后重新运行"])


def report(base: Path, manifest: dict, results: dict[str, dict]) -> None:
    lines = [f"# 多智能体运行报告：{manifest['run_id']}", "", f"**项目**：{manifest['project']}", f"**问题**：{manifest['question']}", "", "## 角色状态", ""]
    for role in ROLE_IDS:
        result = results[role]
        lines.append(f"- `{role}`：**{result['status']}** — {result['summary']}")
    lines.extend(["", "## 证据主张", ""])
    for claim in results["evidence"].get("claims", []):
        lines.append(f"- `{claim['item_id']}` [{claim['status']}] {claim['text']}")
    lines.extend(["", "## 基线指标", ""])
    for metric in results["baseline"].get("metrics", []):
        lines.append(f"- {metric['name']}：方法 {metric['value']}，基线 {metric['baseline']}，差值 {metric['delta']}，通过={metric['passed']}")
    lines.extend(["", "## 红队问题", ""])
    red_issues = results["red_team"].get("issues", [])
    if red_issues:
        lines.extend(f"- [{issue['severity']}] {issue['text']}" for issue in red_issues)
    else:
        lines.append("- 未发现自动检查层面的阻塞问题；仍需人工复核物理假设和数据泄漏。")
    lines.extend(["", "## 下一步", "", "- " + (results["red_team"].get("next_actions") or ["人工审查后决定是否放行"])[0]])
    (base / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_result(result: dict, expected_role: str, run_id: str) -> list[str]:
    errors = []
    required = {"schema_version", "run_id", "role", "status", "created_at", "summary", "claims", "evidence", "metrics", "artifacts", "issues", "next_actions"}
    if set(result) != required:
        errors.append(f"{expected_role}: result fields mismatch")
    if result.get("schema_version") != "0.1.0" or result.get("run_id") != run_id or result.get("role") != expected_role:
        errors.append(f"{expected_role}: identity mismatch")
    if result.get("status") not in RESULT_STATUSES:
        errors.append(f"{expected_role}: invalid status")
    for key in ("claims", "evidence", "metrics", "artifacts", "issues", "next_actions"):
        if not isinstance(result.get(key), list):
            errors.append(f"{expected_role}: {key} must be an array")
    for claim in result.get("claims", []):
        if claim.get("status") not in CLAIM_STATUSES:
            errors.append(f"{expected_role}: invalid claim status")
    return errors


def validate(args: argparse.Namespace) -> None:
    base = run_dir(args.project, args.run)
    manifest = read_json(base / "manifest.json")
    errors = []
    if manifest.get("schema_version") != "0.1.0" or manifest.get("project") != args.project or manifest.get("run_id") != args.run:
        errors.append("manifest identity mismatch")
    for role in ROLE_IDS:
        path = base / "results" / f"{role}.json"
        if not path.is_file():
            errors.append(f"missing result: {role}")
            continue
        errors.extend(validate_result(read_json(path), role, args.run))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Valid run: {args.project}/{args.run}")


def execute(args: argparse.Namespace) -> None:
    base = run_dir(args.project, args.run)
    manifest = read_json(base / "manifest.json")
    paper_ir = Path(args.paper_ir).expanduser().resolve() if args.paper_ir else None
    metrics = Path(args.metrics).expanduser().resolve() if args.metrics else None
    inputs = {"paper_ir": str(paper_ir) if paper_ir else None, "simulation_command": args.simulation_command, "metrics": str(metrics) if metrics else None}
    manifest = update_manifest(base, status="running", inputs=inputs)
    results = {}
    results["evidence"] = evidence_agent(base, manifest, paper_ir)
    results["simulation"] = simulation_agent(base, manifest, args.simulation_command)
    results["baseline"] = baseline_agent(base, manifest, metrics)
    results["red_team"] = red_team_agent(base, manifest, results)
    results["coordinator"] = make_result("coordinator", manifest["run_id"], "completed", "已汇总四个角色结果", issues=[i for role in ("evidence", "simulation", "baseline", "red_team") for i in results[role].get("issues", [])], next_actions=["人工审查 report.md 后决定是否放行"])
    for role, result in results.items():
        write_json(base / "results" / f"{role}.json", result)
    final_status = "completed" if all(results[role]["status"] in {"completed"} for role in ("evidence", "simulation", "baseline", "red_team")) else "blocked"
    role_statuses = [
        {**role, "status": results[role["id"]]["status"]}
        for role in manifest["roles"]
    ]
    update_manifest(base, status=final_status, roles=role_statuses)
    report(base, manifest, results)
    print(f"{final_status}: {base.relative_to(ROOT)}")


def status(args: argparse.Namespace) -> None:
    base = run_dir(args.project, args.run)
    manifest = read_json(base / "manifest.json")
    print(f"{manifest['project']}/{manifest['run_id']} · {manifest.get('status', 'unknown')}")
    for role in ROLE_IDS:
        result = read_json(base / "results" / f"{role}.json")
        print(f"{role}: {result['status']} · {result['summary']}")


def call_role(args: argparse.Namespace) -> None:
    """Run exactly one role, preserving all other role results in the run."""
    base = run_dir(args.project, args.run)
    manifest = read_json(base / "manifest.json")
    role = args.role
    existing = {
        role_id: read_json(base / "results" / f"{role_id}.json")
        for role_id in ROLE_IDS
    }
    blocked_by = [dep for dep in ROLE_DEPENDENCIES[role]
                  if existing[dep].get("status") != "completed"]
    if blocked_by:
        result = make_result(role, args.run, "blocked", "前置角色尚未完成",
                             issues=[{"severity": "high", "text": f"需要先完成: {', '.join(blocked_by)}"}],
                             next_actions=[f"先调用 sr agents call {args.project} --run {args.run} --role {dep}" for dep in blocked_by])
    elif role == "evidence":
        paper_ir = Path(args.paper_ir).expanduser().resolve() if args.paper_ir else None
        result = evidence_agent(base, manifest, paper_ir)
    elif role == "simulation":
        result = simulation_agent(base, manifest, args.simulation_command)
    elif role == "baseline":
        metrics = Path(args.metrics).expanduser().resolve() if args.metrics else None
        result = baseline_agent(base, manifest, metrics)
    elif role == "red_team":
        result = red_team_agent(base, manifest, existing)
    else:
        result = make_result("coordinator", args.run, "completed", "已汇总当前角色结果")
        for role_id in ROLE_IDS:
            existing[role_id] = read_json(base / "results" / f"{role_id}.json")
        report(base, manifest, existing)
    write_json(base / "results" / f"{role}.json", result)
    role_statuses = [{**entry, "status": (result["status"] if entry["id"] == role else entry["status"])}
                     for entry in manifest["roles"]]
    new_status = "completed" if role == "coordinator" and all(
        existing[dep].get("status") == "completed" for dep in ROLE_DEPENDENCIES[role]
    ) else "running"
    update_manifest(base, status=new_status, roles=role_statuses)
    print(f"{role}: {result['status']} · {result['summary']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sr agents")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init"); p.add_argument("project"); p.set_defaults(func=init_project)
    p = sub.add_parser("plan"); p.add_argument("project"); p.add_argument("--question", required=True); p.add_argument("--run"); p.set_defaults(func=plan)
    p = sub.add_parser("run"); p.add_argument("project"); p.add_argument("--run", required=True); p.add_argument("--paper-ir"); p.add_argument("--simulation-command"); p.add_argument("--metrics"); p.set_defaults(func=execute)
    p = sub.add_parser("call"); p.add_argument("project"); p.add_argument("--run", required=True); p.add_argument("--role", required=True, choices=ROLE_IDS); p.add_argument("--paper-ir"); p.add_argument("--simulation-command"); p.add_argument("--metrics"); p.set_defaults(func=call_role)
    p = sub.add_parser("validate"); p.add_argument("project"); p.add_argument("--run", required=True); p.set_defaults(func=validate)
    p = sub.add_parser("status"); p.add_argument("project"); p.add_argument("--run", required=True); p.set_defaults(func=status)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"sr agents: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
