from jobtracker.scrapy_ext import backoff_delay, build_settings


def test_backoff_grows_exponentially_and_is_capped():
    assert [backoff_delay(n, base=2, maximum=60) for n in (1, 2, 3, 4, 5, 6, 7)] == [2, 4, 8, 16, 32, 60, 60]


def test_jitter_stays_within_bounds():
    for _ in range(50):
        assert 4 <= backoff_delay(2, base=2, maximum=60, jitter=1.0) <= 5


def test_polite_scraping_settings(cfg):
    settings = build_settings(cfg)
    assert settings["AUTOTHROTTLE_ENABLED"] is True and settings["ROBOTSTXT_OBEY"] is True
    assert settings["DOWNLOAD_DELAY"] >= 1 and settings["RETRY_TIMES"] >= 1
    assert "Mozilla/5.0" in settings["USER_AGENT"]
    assert settings["DOWNLOADER_MIDDLEWARES"]["jobtracker.scrapy_ext.BackoffRetryMiddleware"] == 550
