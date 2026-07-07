import re

from trading.local_arb.config import DEFAULT_CONFIG_PATH, config_hash, git_sha, load_config


def test_load_default_config():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg["engine_role"] == "local_arb_research"
    assert set(cfg["exchanges"]) == {"mercadobitcoin", "novadax", "bitypreco"}
    assert cfg["pair"]["symbol"] == "USDT/BRL"


def test_config_hash_deterministic_and_order_insensitive():
    a = {"x": 1, "y": {"b": 2, "a": 3}}
    b = {"y": {"a": 3, "b": 2}, "x": 1}
    assert config_hash(a) == config_hash(b)
    assert re.fullmatch(r"[0-9a-f]{64}", config_hash(a))


def test_config_hash_changes_on_edit():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    h1 = config_hash(cfg)
    cfg["thresholds"]["min_net_bps"] = 999.0
    assert config_hash(cfg) != h1


def test_git_sha_in_repo():
    sha = git_sha()
    assert sha == "unknown" or re.fullmatch(r"[0-9a-f]{7,40}", sha)


def test_git_sha_fallback_outside_repo(tmp_path):
    assert git_sha(tmp_path) == "unknown"


def test_report_thesis_states():
    from trading.local_arb.report import thesis_status

    assert thesis_status(5, 20.0, 1.0).status == "HOLD"
    assert thesis_status(30, -3.0, 1.0).status == "MORTA"
    assert thesis_status(30, 5.0, 1.0).status == "FERIDA"       # net positivo mas abaixo do alvo
    assert thesis_status(30, 20.0, 50.0).status == "FERIDA"     # decay alto
    assert thesis_status(30, 20.0, 5.0).status == "VIVA"
