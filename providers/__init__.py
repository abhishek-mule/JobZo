from providers.base import JobProvider, RawJob
from providers.rss import RSSProvider
from providers.company_pages import CompanyPagesProvider
from providers.manual import ManualProvider
from providers.telegram import TelegramProvider

PROVIDERS: dict[str, type[JobProvider]] = {
    "rss": RSSProvider,
    "company_pages": CompanyPagesProvider,
    "manual": ManualProvider,
    "telegram": TelegramProvider,
}
