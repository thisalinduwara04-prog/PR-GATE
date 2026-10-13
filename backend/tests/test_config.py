from app.config import Settings


def test_watched_repos_parsed_from_comma_list():
    s = Settings(watched_repos=" alice/demo , Bob/Other ,")
    assert s.watched_repo_set == {"alice/demo", "bob/other"}


def test_defaults():
    s = Settings()
    assert s.verify_timeout_s == 600
    assert s.publish_min_severity == "medium"
    assert s.db_path == "/data/pr-gate.db"
