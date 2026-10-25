from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'src'))
from src.layout import project_path


def test_csv_write_is_outside_runtime_and_existing_state_stays_in_runtime(tmp_path,monkeypatch):
    runtime=tmp_path/'02-代码运行'
    runtime.mkdir()
    state=runtime/'data/state.json'
    state.parent.mkdir()
    state.write_text('{"notification_keys":["already-sent"]}',encoding='utf-8')
    monkeypatch.chdir(runtime)
    output=project_path(Path('.'),'csv/01_重点关注/example.csv')
    output.parent.mkdir(parents=True)
    output.write_text('检查时间,状态\n2026-10-26,正常\n',encoding='utf-8-sig')
    assert output == tmp_path/'01-CSV/01_重点关注/example.csv'
    assert not (runtime/'csv').exists()
    assert project_path(runtime,'data/state.json')==state
    assert 'already-sent' in state.read_text(encoding='utf-8')
    assert project_path(runtime,'.github/workflows/monitor.yml')==tmp_path/'.github/workflows/monitor.yml'


def test_explicit_paths_and_legacy_test_workspaces_are_preserved(tmp_path):
    absolute=tmp_path/'explicit.csv'
    assert project_path(tmp_path/'02-代码运行',absolute)==absolute
    assert project_path(tmp_path,'csv/result.csv')==tmp_path/'csv/result.csv'
