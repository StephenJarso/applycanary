"""work.ke — curated entry-level technology roles in Kenya.

    https://work.ke/jobs/entry-level-technology

work.ke aggregates Kenyan vacancies and links each one to the employer's own
advert. The connector reads its small entry-level technology and junior-tech
collections, then pulls the official apply link and the plain-English role
summary from each detail page. No applications are sent through work.ke.

Marked as a scraper: the listing is server-rendered HTML, so a redesign can
break the parser even though the underlying data is public.
"""

from __future__ import annotations

from app.entry_level import fetch_entry_level
from app.sources.base import BaseSource, RawJob, register


@register
class WorkKESource(BaseSource):
    name = "workke"
    is_scraper = True

    async def fetch(self) -> list[RawJob]:
        feed = await fetch_entry_level(self.client)
        jobs: list[RawJob] = []
        for role in feed["roles"]:
            jobs.append(
                RawJob(
                    source=self.name,
                    source_id=role["slug"],
                    company=role["employer"],
                    title=role["title"],
                    location=role["location"],
                    description=role["description"],
                    apply_url=role["apply_url"] or role["source_url"],
                    remote_flag=role["is_remote"],
                    extra={
                        "category": role["category"],
                        "seniority": role["seniority"],
                        "work_mode": role["work_mode"],
                        "listed_via": role["source_url"],
                    },
                )
            )
        return jobs
