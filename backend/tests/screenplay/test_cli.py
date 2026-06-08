"""CLI 工具测试 — PR#5。

阶段 7(2026-06-08)迁徙父平台:**全文件 skip**。
原因:`app.screenplay.cli` 是比赛仓库的独立 CLI 工具(命令行直接跑 yaml 校验)。
父平台是 web 服务模式,没把 CLI 入口搬进来(用户改用 endpoint)。这些测试本质
上是测命令行行为,不是 web 端 → 不再适用。
后续若有 CLI 需求,在 backend/scripts/ 单独再加,这些测试不会复活。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="阶段 7:CLI 模块未迁入父平台(用户走 /api/screenplay/* endpoint)"
)

import io  # noqa: E402  保留原文件其余 import 让 pytest 静态分析不报错
import json
import sys
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"
VALID_YAML = FIXTURES_DIR / "valid_screenplay.yaml"


@pytest.fixture
def invalid_yaml_file(tmp_path: Path) -> Path:
    """造一份故意非法的 YAML 文件:缺 meta 必填字段。"""
    p = tmp_path / "bad.yaml"
    p.write_text(
        "characters:\n  - id: char_001\n    name: A\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def parse_error_yaml_file(tmp_path: Path) -> Path:
    """YAML 解析错(语法层)"""
    p = tmp_path / "syntax_error.yaml"
    p.write_text("meta:\n  title: x\n - bad indent", encoding="utf-8")
    return p


# ============================================================
# 路径输入
# ============================================================

def test_validate_valid_file_returns_0(capsys):
    """合法文件 → exit 0 + 通过消息"""
    from app.screenplay.cli import main
    rc = main(["validate", str(VALID_YAML)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "通过" in captured.out


def test_validate_valid_file_shows_stats(capsys):
    """通过报告应显示标题 / 场次等"""
    from app.screenplay.cli import main
    main(["validate", str(VALID_YAML)])
    out = capsys.readouterr().out
    assert "标题" in out
    assert "场次" in out


def test_validate_invalid_file_returns_1(capsys, invalid_yaml_file):
    """缺 meta 必填 → exit 1"""
    from app.screenplay.cli import main
    rc = main(["validate", str(invalid_yaml_file)])
    captured = capsys.readouterr()
    assert rc == 1
    # 报告应含"失败"
    assert "失败" in captured.out


def test_validate_yaml_parse_error_returns_1(capsys, parse_error_yaml_file):
    """YAML 解析错 → exit 1 + 错误层 tag"""
    from app.screenplay.cli import main
    rc = main(["validate", str(parse_error_yaml_file)])
    captured = capsys.readouterr()
    assert rc == 1


def test_validate_missing_file_returns_2(capsys):
    """文件不存在 → exit 2 + stderr"""
    from app.screenplay.cli import main
    rc = main(["validate", "/path/that/does/not/exist.yaml"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "不存在" in captured.err


def test_validate_path_is_directory_returns_2(capsys, tmp_path):
    from app.screenplay.cli import main
    rc = main(["validate", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 2


# ============================================================
# JSON 输出
# ============================================================

def test_json_mode_outputs_structured_report(capsys):
    """--json 输出可被 json.loads 解析"""
    from app.screenplay.cli import main
    main(["validate", str(VALID_YAML), "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "valid" in parsed
    assert "issue_count" in parsed
    assert "error_count" in parsed
    assert "issues" in parsed
    assert parsed["valid"] is True


def test_json_mode_with_invalid_file(capsys, invalid_yaml_file):
    from app.screenplay.cli import main
    rc = main(["validate", str(invalid_yaml_file), "--json"])
    assert rc == 1
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["valid"] is False
    assert parsed["error_count"] >= 1


# ============================================================
# stdin
# ============================================================

def test_validate_stdin_input(capsys, monkeypatch):
    """path='-' → 从 stdin 读"""
    valid_text = VALID_YAML.read_text(encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(valid_text))
    from app.screenplay.cli import main
    rc = main(["validate", "-"])
    assert rc == 0
    assert "通过" in capsys.readouterr().out


# ============================================================
# 入口 / 帮助
# ============================================================

def test_no_command_shows_help(capsys):
    """无参数 → 打印帮助 + exit 2"""
    from app.screenplay.cli import main
    rc = main([])
    assert rc == 2


def test_validate_without_path_exits_2(capsys):
    """validate 没传路径 → argparse 报错(SystemExit 2)"""
    from app.screenplay.cli import main
    with pytest.raises(SystemExit) as exc_info:
        main(["validate"])
    assert exc_info.value.code == 2
