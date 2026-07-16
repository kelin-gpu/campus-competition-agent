import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_agent_config_is_valid_and_tool_names_are_unique():
    config_path = PROJECT_ROOT / "config" / "agent_llm_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert isinstance(config.get("sp"), str) and config["sp"].strip()
    tools = config.get("tools")
    assert isinstance(tools, list) and tools
    assert len(tools) == len(set(tools))


def test_platform_owned_files_are_present():
    protected_paths = (
        "src/main.py",
        "src/utils/file/file.py",
        "src/storage/database/db.py",
        "src/storage/database/shared/model.py",
        "src/storage/database/supabase_client.py",
        "src/storage/memory/memory_saver.py",
        "src/storage/s3/s3_storage.py",
        "scripts/setup.sh",
        ".coze",
        "pyproject.toml",
    )

    missing = [path for path in protected_paths if not (PROJECT_ROOT / path).exists()]
    assert not missing, f"missing platform-owned files: {missing}"
