"""Events, programs, internships and volunteer slots for the public pages.

Served through ``GET /api/public/opportunities`` (events and paid programs),
``/api/public/internships`` and ``/api/public/volunteers``.

The page is fed by two very different kinds of data:

* **Events** come from two public calendars. Tech Group Kenya's site is
  server-rendered HTML carrying one ``event-card`` anchor per event, and each
  event's page carries schema.org/Event JSON-LD with description, ISO start
  time, organiser, city and ticket price. Their JSON endpoint is key-gated, so
  HTML it is. GDG chapters publish through Bevy's public API, which catches
  events (like DevFest Kisumu) that never reach the TGK calendar. Both fetches
  are polite and independently fault-tolerant; a failure in one does not
  erase the other.

* **Paid programs and internships** have no public API anywhere: application
  windows and compensation are published as prose on each site. They are
  curated here, server side, so adding one is a backend change rather than a
  frontend rebuild. Compensation is stated explicitly ("Paid:", "Unpaid +
  benefits:") and tagged, and internships are split out under their own kind
  for the dedicated tab.

* **Volunteer and speaking slots** are the rarest of all, and the least
  structured: a conference advertises its registration crew or its call for
  speakers in a line of prose on its own site, and nothing aggregates them.
  They are curated here like the paid programs, and the live event calendars
  are additionally scanned for volunteer and speaker calls the organisers
  wrote into the event description itself.

A failed fetch never fails the request. The last good event list keeps being
served for ``STALE_TTL`` and ``events_live`` reports the difference, so the UI
can say the feed is stale instead of implying Kenya stopped holding events.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone

import httpx
from sqlmodel import Session, select

from app.db import session_scope
from app.models import ProgrammeEntry

log = logging.getLogger(__name__)

INDEX_URL = "https://events.techgroupkenya.co.ke/events/"
GDG_API = "https://gdg.community.dev/api/event_slim/for_chapter/{chapter}/"
# Chapter ids are stable in Bevy URLs; city is used when the slim payload has
# no venue. Add a chapter here to widen the feed.
GDG_CHAPTERS: dict[int, tuple[str, str]] = {
    841: ("GDG Kisumu", "Kisumu"),
    826: ("GDG Nairobi", "Nairobi"),
    816: ("GDG Eldoret", "Eldoret"),
    554: ("GDG Kisii", "Kisii"),
}
GDG_FIELDS = (
    "title,start_date,end_date,url,description,description_short,"
    "chapter_title,event_type_title,cohost_registration_url"
)
GDG_SOURCE = "GDG Community"
EAT = timezone(timedelta(hours=3))
# Identify the client honestly, as the job connectors do.
USER_AGENT = "applycanary/0.1 (personal job search agent; +https://github.com)"
SOURCE_NAME = "TGK Events"
FALLBACK_ORG = "Tech Group Kenya"

CACHE_TTL = 24 * 3600.0      # events/programs change rarely: refresh once a day
DETAIL_TTL = 6 * 3600.0      # an event's own page rarely changes once published
DETAIL_MISS_TTL = 600.0      # a failing detail page is retried within 10 minutes
STALE_TTL = 7 * 24 * 3600.0  # keep last-known events through a multi-day outage
ENRICH_CONCURRENCY = 4       # parallel detail requests; be a polite guest
REQUEST_TIMEOUT = 20.0
MAX_EVENTS = 40
# Worst case for one cold fetch: a 20 s index timeout + ceil(MAX_EVENTS/4)
# waves of 20 s detail timeouts ≈ 3.5 min. No visitor waits inside that — a
# first page load verifies the cache cold for at most COLD_WAIT and then
# serves the programs with a degraded banner while the fetch completes in
# the background (boot warm-up + the 15-min scheduler job). Render's edge
# and browsers both cut stalled responses well before one minute.
COLD_WAIT = 15.0


# ------------------------------------------------------------- paid programs
#
# Curated server-side (see the module docstring). Shapes match
# `OpportunityOut` in app/api/router.py; the frontend derives its Location and
# Interest filters from whatever the API returns, so appending an entry here
# is the whole change.

PAID_PROGRAMS: list[dict] = [
    {
        "kind": "Paid program",
        "title": "Google Summer of Code",
        "org": "Google",
        "when": "Coding May–Aug",
        "opens": "2027-03-16",
        "closes": "2027-04-07",
        "steps": [
            "Orgs announced ~late Feb",
            "Contributor apps mid-Mar → early Apr",
            "Results ~May",
            "Coding May–Aug",
        ],
        "city": "Online",
        "tags": ["Open source", "Students", "Beginners"],
        "stipend": "USD 750–6,600 (by region & size)",
        "source": "Official",
        "url": "https://summerofcode.withgoogle.com/",
        "desc": "Paid, mentored open-source projects. Open to students and beginners in open source.",
    },
    {
        "kind": "Paid program",
        "title": "Outreachy (May 2027 cohort)",
        "org": "Software Freedom Conservancy",
        "when": "Internship May–Aug",
        "opens": "2027-01-15",
        "closes": "2027-02-05",
        "steps": [
            "Initial application ~mid-Jan → early Feb",
            "Contribution period Mar",
            "Interns announced ~Apr",
            "Internship May–Aug",
        ],
        "city": "Online",
        "tags": ["Paid", "Open source", "Women in tech", "Beginners"],
        "stipend": "Paid: USD 7,000 stipend",
        "source": "Official",
        "url": "https://www.outreachy.org/",
        "desc": "Remote, 3-month open-source internships for people underrepresented in tech. Kenyans eligible.",
    },
    {
        "kind": "Paid program",
        "title": "Outreachy (Dec 2027 cohort)",
        "org": "Software Freedom Conservancy",
        "when": "Internship Dec–Mar",
        "opens": "2027-08-01",
        "closes": "2027-08-25",
        "steps": [
            "Initial application ~Aug",
            "Contribution period Oct",
            "Interns announced ~Nov",
            "Internship Dec–Mar",
        ],
        "city": "Online",
        "tags": ["Paid", "Open source", "Women in tech", "Beginners"],
        "stipend": "Paid: USD 7,000 stipend",
        "source": "Official",
        "url": "https://www.outreachy.org/",
        "desc": "Second yearly Outreachy cohort, same remote format and stipend.",
    },
    {
        "kind": "Paid program",
        "title": "LFX Mentorship (Spring term)",
        "org": "Linux Foundation",
        "when": "Mentorship Mar–May",
        "opens": "2027-01-20",
        "closes": "2027-02-10",
        "steps": [
            "Projects posted ~Jan",
            "Apply ~late Jan → mid-Feb",
            "Mentorship Mar–May",
            "Summer & Fall terms follow",
        ],
        "city": "Online",
        "tags": ["Open source", "Cloud", "Backend"],
        "stipend": "USD 3,000–6,600",
        "source": "Official",
        "url": "https://mentorship.lfx.linuxfoundation.org/",
        "desc": "Paid mentorships on CNCF, Kubernetes, Linux kernel and other LF projects.",
    },
    {
        "kind": "Paid program",
        "title": "LF Decentralized Trust Mentorship",
        "org": "Linux Foundation",
        "when": "Mentorship Jun–Nov",
        "opens": "2027-02-01",
        "closes": "2027-03-15",
        "steps": ["Projects posted ~Feb", "Apply Feb → Mar", "Mentorship Jun–Nov"],
        "city": "Online",
        "tags": ["Open source", "Blockchain", "Backend"],
        "stipend": "USD 3,000–6,600",
        "source": "Official",
        "url": "https://www.lfdecentralizedtrust.org/mentorship",
        "desc": "Paid, part- or full-time mentorship on Hyperledger and other decentralized-trust projects.",
    },
    {
        "kind": "Paid program",
        "title": "Summer of Bitcoin",
        "org": "Summer of Bitcoin",
        "when": "Internship May–Aug",
        "opens": "2027-01-10",
        "closes": "2027-02-28",
        "steps": ["Apply ~Jan → Feb", "Skill test & proposals Mar–Apr", "Internship May–Aug"],
        "city": "Online",
        "tags": ["Paid", "Bitcoin", "Open source", "Students"],
        "stipend": "Paid: USD 3,000 stipend",
        "source": "Official",
        "url": "https://www.summerofbitcoin.org/",
        "desc": "Global summer internship in Bitcoin open-source development for university students.",
    },
    {
        "kind": "Paid program",
        "title": "Btrust Builders",
        "org": "Btrust",
        "when": "Cohort-based",
        "steps": ["Watch announcements", "Apply when a cohort opens", "Training + funded contributions"],
        "city": "Online",
        "tags": ["Bitcoin", "Open source", "Backend"],
        "stipend": "Stipend + grants",
        "source": "Official",
        "url": "https://www.btrust.tech/",
        "desc": "Trains and funds African developers contributing to Bitcoin open source.",
    },
    {
        "kind": "Paid program",
        "title": "MLH Fellowship",
        "org": "Major League Hacking",
        "when": "Spring, Summer & Fall batches",
        "steps": ["Rolling applications", "Interview + technical step", "12-week batch"],
        "city": "Online",
        "tags": ["Open source", "Students", "Web"],
        "stipend": "Need-based stipend",
        "source": "Official",
        "url": "https://fellowship.mlh.io/",
        "desc": "12-week remote program: contribute to open source with a mentor and a pod.",
    },
    {
        "kind": "Paid program",
        "title": "Google Season of Docs",
        "org": "Google",
        "when": "Projects run ~May–Nov",
        "opens": "2027-02-01",
        "closes": "2027-04-01",
        "steps": ["Orgs apply ~Feb–Mar", "Writers contact orgs Mar–May", "Docs work ~May–Nov"],
        "city": "Online",
        "tags": ["Open source", "Technical writing"],
        "stipend": "Paid by org grant",
        "source": "Official",
        "url": "https://developers.google.com/season-of-docs",
        "desc": "Technical writers get paid to improve open-source documentation.",
    },
    {
        "kind": "Paid program",
        "title": "Igalia Coding Experience",
        "org": "Igalia",
        "when": "Flexible 12-week projects",
        "steps": ["Rolling applications", "Pick browser/graphics/compilers area", "Remote mentored work"],
        "city": "Online",
        "tags": ["Open source", "Students", "Systems"],
        "stipend": "EUR 6,500",
        "source": "Official",
        "url": "https://www.igalia.com/coding-experience/",
        "desc": "Paid, mentored remote work on browsers, graphics and compilers.",
    },
    {
        "kind": "Paid program",
        "title": "ALX Software Engineering",
        "org": "ALX Africa",
        "when": "Rolling intakes",
        "steps": ["Apply online anytime", "Pre-course", "Program start"],
        "city": "Kenya",
        "tags": ["Beginners", "Web", "Backend"],
        "stipend": "Free tuition",
        "source": "Official",
        "url": "https://www.alxafrica.com/",
        "desc": "Free intensive training for African learners in software, data and cloud.",
    },
    {
        "kind": "Paid program",
        "title": "Power Learn Project",
        "org": "PLP Africa",
        "when": "Cohort-based",
        "steps": ["Apply when cohort opens", "16-week software training", "Hackathons & placements"],
        "city": "Kenya",
        "tags": ["Beginners", "Web"],
        "stipend": "Free / sponsored",
        "source": "Official",
        "url": "https://www.plpacademy.com/",
        "desc": "Sponsored 16-week software training for young Africans, strong presence in Kenya.",
    },
    {
        "kind": "Paid program",
        "title": "Presidential Digital Talent Programme",
        "org": "ICT Authority Kenya",
        "when": "1-year internship · yearly cohort",
        "opens": "2027-08-01",
        "closes": "2027-09-06",
        "steps": [
            "Cohort applications open ~Aug (DigiTalent portal)",
            "Screening & shortlisting ~Sep–Oct",
            "Placement starts ~Nov",
            "1 year across MDAs & partner institutions",
        ],
        "city": "Kenya",
        "tags": ["Paid", "Careers", "Students", "Government"],
        "stipend": "Paid: government stipend (1 year)",
        "source": "Official",
        "url": "https://ict.go.ke/presidential-digital-talent-program-pdtp",
        "desc": "Kenya's paid digital internship: unemployed youth aged 21–29 spend a year in public and partner institutions, with a government stipend.",
    },
    {
        "kind": "Paid program",
        "title": "AWS re/Start",
        "org": "Amazon Web Services",
        "when": "Rolling cohorts",
        "steps": [
            "Apply through a local training partner",
            "12 weeks full-time labs & lessons",
            "Cloud Practitioner exam prep",
            "Job-search support & placements",
        ],
        "city": "Kenya",
        "tags": ["Cloud", "Beginners", "Certification"],
        "stipend": "Free training (stipends on some cohorts)",
        "source": "Official",
        "url": "https://aws.amazon.com/training/restart/",
        "desc": "Free 12-week full-time cloud training for unemployed and underemployed learners, run with local partners in Kenya.",
    },
    {
        "kind": "Paid program",
        "title": "Internet Society Fellowship 2027",
        "org": "Internet Society",
        "when": "10-month fellowship · Feb–Dec 2027",
        "opens": "2026-09-23",
        "closes": "2026-12-18",
        "steps": [
            "Enrol in pre-fellowship training by 22 Nov 2026",
            "Complete the modules to receive the application link",
            "Apply by 18 Dec 2026 (rolling once modules are done)",
            "Notifications 19 Feb 2027 · kick-off 22 Feb 2027",
            "Mentored project Jun–Nov 2027",
        ],
        "city": "Online",
        "tags": ["Paid", "Fellowship", "Internet policy", "Security", "Africa"],
        "stipend": "Paid: up to USD 5,000 project stipend + event travel support",
        "source": "Official",
        "url": "https://www.internetsociety.org/fellowship/",
        "desc": (
            "10-month Internet leadership fellowship across policy, connectivity "
            "and safety/security tracks, with a mentor, project funding and travel "
            "to a global or regional Internet event. Open to applicants from any "
            "country; the self-paced pre-fellowship training (23 Sep–22 Nov 2026) "
            "must be completed before the application link is issued."
        ),
    },
    {
        "kind": "Paid program",
        "title": "GitHub Campus Experts",
        "org": "GitHub",
        "when": "Annual cohort",
        "steps": [
            "Applications open ~mid-year",
            "Application form + video resume",
            "Training, then grow your campus community",
        ],
        "city": "Online",
        "tags": ["Students", "Leadership", "Open source"],
        "stipend": "Training + event funding",
        "source": "Official",
        "url": "https://github.com/education/students/campus-expert",
        "desc": "Student leaders get free training, resources and funding to run tech communities at their institution.",
    },
    {
        "kind": "Paid program",
        "title": "MEST AI Startup Program",
        "org": "MEST Africa",
        "when": "7 months training + 4 months incubation · residential, Accra",
        "opens": "2027-05-01",
        "closes": "2027-07-20",
        "steps": [
            "Applications open around May on meltwater.org — the 2027 class closed on 20 Jul 2026",
            "Apply online; shortlisted applicants interview in Ghana, Nigeria, Senegal or Kenya",
            "7 months of full-time training: software engineering, AI, product and business",
            "Selected teams continue into 4 months of incubation and can pitch for pre-seed funding",
        ],
        "city": "Accra",
        "tags": ["Paid", "Fully funded", "Accommodation", "Flights", "AI", "Software", "Africa"],
        "stipend": (
            "Paid: fully sponsored — tuition, accommodation, meals, healthcare "
            "and the flight to Ghana"
        ),
        "source": "Official",
        "url": "https://meltwater.org/mest-ai-startup-program",
        "desc": (
            "MEST has trained African software founders in Accra since 2008, "
            "and the AI Start-up Program is its current form: seven months of "
            "full-time, in-person training in software engineering and AI, "
            "then up to four months of incubation for the teams that earn it. "
            "Nobody pays a fee — tuition, accommodation, meals and healthcare "
            "are covered, and admitted participants get their flight to Ghana "
            "paid. The 2027 intake drew from West and East Africa with "
            "interviews held in Nairobi, so Kenya is in scope; the catch is "
            "that it is residential and full-time, and cannot be combined "
            "with a job or a degree."
        ),
    },
]

# Curated entries can be internships as well as pay-to-learn programs. Keeping
# the same shape lets the page reuse one card component; the dedicated
# Internship tab is just a `kind` filter. Compensation is stated explicitly —
# "Paid:", "Unpaid + benefits:" — and tagged, so paying and unpaid placements
# are distinguishable at a glance.
_INTERNSHIP_TITLES = {
    "Outreachy (May 2027 cohort)",
    "Outreachy (Dec 2027 cohort)",
    "Summer of Bitcoin",
    "Presidential Digital Talent Programme",
}

INTERNSHIPS: list[dict] = [
    {**program, "kind": "Internship"}
    for program in PAID_PROGRAMS
    if program["title"] in _INTERNSHIP_TITLES
]
INTERNSHIPS.append(
    {
        "kind": "Internship",
        "title": "HENNGE Global Internship Program (GIP)",
        "org": "HENNGE K.K.",
        "when": "4–6 weeks · rolling batches",
        "opens": None,
        "closes": None,
        "steps": [
            "Register and verify your email",
            "Solve the coding challenge, submit CV + cover letter",
            "HR interview, then a technical interview",
            "Pick a Full-Stack or Front-End pathway and join a batch",
        ],
        "city": "Tokyo",
        "tags": ["Unpaid", "Benefits", "Japan", "Go", "Students", "On-site"],
        "stipend": "Unpaid + benefits: monthly subsidy, round-trip airfare, medical insurance, mobile, visa support",
        "source": "Official",
        "url": "https://recruit.hennge.com/en/gip/",
        "desc": "On-site, English-speaking software engineering internship in Tokyo. Full-Stack pathway uses Python, Go or TypeScript (React/Vue), front-end on TypeScript, with AWS and DevOps training. Slots are first-come, first-served, and outstanding interns may be offered full-time roles.",
    }
)

# Global and Kenyan internships that no job board indexes, or indexes only per
# vacancy. Compensation and eligibility are quoted from each programme's own
# page: those are the two facts that decide whether an afternoon of applying is
# worth it, and both are the kind that scholarship blogs republish wrong.
INTERNSHIPS.extend(
    [
        {
            "kind": "Internship",
            "title": "UNHCR Internship Programme",
            "org": "UNHCR – UN Refugee Agency",
            "when": "2–6 months · extendable to 8 full-time",
            "opens": None,
            "closes": None,
            "steps": [
                "Create a profile on the UNHCR recruitment portal",
                "Look for internship vacancies at a duty station you can work from",
                "Each vacancy sets its own deadline — there is no single annual intake",
                "Some positions can be done remotely, subject to approval",
            ],
            "city": "Various",
            "tags": ["Paid", "Internship", "United Nations", "NGO", "Remote", "Global"],
            "stipend": (
                "Paid: monthly allowance (part-time at 50%), food and local "
                "transport support, return travel for eligible interns"
            ),
            "source": "Official",
            "url": "https://www.unhcr.org/us/get-involved/work-us/careers-unhcr/internships",
            "desc": (
                "UNHCR recruits interns year-round rather than in one intake: an "
                "internship can begin at any time, normally runs two to six "
                "months, and can be extended up to eight months full-time. "
                "Part-time internships run longer, and their allowance is "
                "calculated at 50% of the full-time rate for the duty station. "
                "Because deadlines are set per vacancy, this is one to watch "
                "rather than wait on — and the same portal carries the "
                "UN Volunteering and consultant roles that never make it to "
                "aggregators."
            ),
        },
        {
            "kind": "Internship",
            "title": "UNICEF Internship Programme",
            "org": "UNICEF",
            "when": "6–26 weeks · full-time or part-time",
            "opens": None,
            "closes": None,
            "steps": [
                "Browse the UNICEF vacancies page — internships run all year",
                "Check the requirements: 18+, enrolled or graduated within two years",
                "Apply to the specific vacancy with a profile tailored to it",
                "Only shortlisted candidates are contacted",
            ],
            "city": "Various",
            "tags": ["Paid", "Internship", "United Nations", "NGO", "Global"],
            "stipend": (
                "Paid: monthly stipend, plus a one-time travel and visa "
                "contribution when funding allows"
            ),
            "source": "Official",
            "url": "https://www.unicef.org/careers/internships",
            "desc": (
                "UNICEF pays interns a monthly stipend and adds a one-time lump "
                "sum towards travel and visa costs when funding is available, "
                "which puts it among the more accessible UN schemes. Placements "
                "last six to twenty-six weeks and may be full- or part-time, "
                "and they are offered throughout the year rather than in one "
                "window. Open to anyone at least 18 who is enrolled in an "
                "undergraduate, graduate or PhD programme, or graduated within "
                "the past two years, and who has no immediate relative working "
                "for UNICEF."
            ),
        },
        {
            "kind": "Internship",
            "title": "WFP Internship Programme",
            "org": "World Food Programme",
            "when": "2–8 months · country offices, regional bureaux or HQ",
            "opens": None,
            "closes": None,
            "steps": [
                "Check the timing rule: enrolled with classes attended in the last 12 months, or graduated within the last 6",
                "Two years of undergraduate study must be completed",
                "Apply to a specific internship in the WFP openings list",
                "Placements run at country, regional or headquarters level",
            ],
            "city": "Various",
            "tags": ["Paid", "Internship", "United Nations", "NGO", "Global"],
            "stipend": (
                "Paid: up to USD 1,000/month, medical and accident insurance, "
                "return trip for eligible interns"
            ),
            "source": "Official",
            "url": "https://www.wfp.org/careers/contract-types",
            "desc": (
                "WFP internships run two to eight months and come with a stipend "
                "capped at USD 1,000 a month plus medical and accident "
                "insurance. The clause Kenyan applicants should read first is "
                "the roundtrip appointment and repatriation trip, which covers "
                "nationals of developing economies studying in their own "
                "country. Eligibility is strict about timing: you must be "
                "enrolled and have attended classes in the past 12 months, or "
                "have graduated within the last six months."
            ),
        },
        {
            "kind": "Internship",
            "title": "Safaricom Internship Program",
            "org": "Safaricom PLC",
            "when": "3–6 months · annual intake",
            "opens": None,
            "closes": None,
            "steps": [
                "Search the Safaricom careers portal for the current intake",
                "Create a profile and finish the application in one session",
                "Sit the online assessment (psychological, technical, competency)",
                "Screening → assessment → selection → confirmation by email",
            ],
            "city": "Nairobi",
            "tags": ["Internship", "Kenya", "Telecom", "On-site"],
            "stipend": "Stipend not published by Safaricom",
            "source": "Official",
            "url": "https://www.safaricom.co.ke/career/internship-program",
            "desc": (
                "Kenya's largest telco runs its internship across every business "
                "function, three to six months at a time. You qualify from the "
                "third year of a degree, while awaiting graduation, or within "
                "12 months of graduating. Selection is a four-stage funnel — "
                "screening, online assessment, shortlist, confirmation — and "
                "interns are added to the internal talent pool with priority "
                "for new roles, so this is a genuine route in rather than a "
                "certificate."
            ),
        },
        {
            "kind": "Internship",
            "title": "Google Software Engineering Internship 2027",
            "org": "Google",
            "when": "13 weeks minimum, Summer 2027 · apply by ~2 Dec",
            "opens": None,
            "closes": "2026-12-02",
            "steps": [
                "Open Google's internships page and search the 2027 openings",
                "Pick a location and team you could actually work in",
                "Apply before the December deadline — this one closes on a date, not on a rolling basis",
                "Interviews follow; internships start May, June or July 2027",
            ],
            "city": "Various",
            "tags": ["Paid", "Internship", "Software", "AI", "Global"],
            "stipend": "Paid: Google internship compensation, stated on each posting",
            "source": "Official",
            "url": "https://www.google.com/about/careers/applications/internships",
            "desc": (
                "The 2027 software engineering internships are open and the "
                "funnel shuts early: Google's student channel announced a "
                "2 December application deadline, and interns must be available "
                "full-time for at least 13 weeks over Summer 2027, starting in "
                "May, June or July. Roles are listed by location, so check that "
                "an opening accepts applicants where you are before spending "
                "the afternoon on it. The process is interview-led rather than "
                "degree-led."
            ),
        },
    ]
)

# Funded placements. HENNGE is the template — an internship worth taking
# because it pays for the flight and the room — so these are the programmes
# that answer the same question: what does it cost me to say yes? Each one
# states the funding first, then what travel or housing comes with it. Huawei
# and UNON are the local routes; Grafana is the Go-shaped one that does not
# care where you sit.
INTERNSHIPS.extend(
    [
        {
            "kind": "Internship",
            "title": "Huawei Seeds for the Future (Kenya)",
            "org": "Huawei Kenya & ICT Authority",
            "when": "~3 weeks · annual call, usually Mar–May",
            "opens": "2027-03-01",
            "closes": None,
            "steps": [
                "Watch Huawei Kenya's site and socials, and ask your ICT department — universities are nominated",
                "Register through the yearly Huawei Kenya form when it opens (the 2025 call closed on 7 May)",
                "Take a week of online HCIA certification training",
                "A week of training in Nairobi, then a funded training trip abroad with the cohort",
            ],
            "city": "Kenya",
            "tags": ["Paid", "Travel covered", "Accommodation", "Kenya", "Students", "Cloud"],
            "stipend": (
                "Paid: fully funded — training, travel and accommodation covered, "
                "no fee to attend"
            ),
            "source": "Official",
            "url": "https://www.huawei.com/ke/corporate-citizenship/seeds-for-the-future",
            "desc": (
                "Huawei's flagship ICT student programme, run with the ICT "
                "Authority in Kenya since 2014. Recent editions are a "
                "three-week arc: a week of virtual HCIA training, a week on "
                "site in Nairobi, then a week of training and a digital tour "
                "abroad — the flights and the accommodation are the "
                "programme's cost, not the student's. Places are few (around "
                "ten in the physical editions), so the nomination route "
                "through your university matters as much as the public form."
            ),
        },
        {
            "kind": "Internship",
            "title": "UNEP & UN-Habitat internships (Nairobi)",
            "org": "United Nations Office at Nairobi",
            "when": "2–6 months · vacancies posted year-round",
            "opens": None,
            "closes": None,
            "steps": [
                "Create an Inspira account on the UN Careers portal",
                "Search internships with duty station Nairobi and filter the IT / data job family",
                "Apply to each vacancy separately — every posting carries its own deadline",
                "The same programme covers UNEP, UN-Habitat, UNON and the UN Dispute Tribunal",
            ],
            "city": "Nairobi",
            "tags": ["Internship", "United Nations", "Kenya", "On-site", "Data", "IT"],
            "stipend": (
                "Stipend not published — the standard UN internship leaves "
                "travel, visa, accommodation and living costs to the intern"
            ),
            "source": "Official",
            "url": "https://www.unon.org/en/content/internship-programme",
            "desc": (
                "The UN Office at Nairobi runs one internship programme for "
                "UNEP, UN-Habitat and UNON itself, which makes Nairobi the "
                "densest concentration of UN internships in Africa. Postings "
                "run from two to six months and are advertised year-round "
                "rather than in one intake, and the Nairobi list regularly "
                "carries information-technology, data and software roles "
                "alongside the policy ones. Read the funding line before you "
                "apply: no stipend is published, so this is a name and a "
                "reference for your CV, not an income."
            ),
        },
        {
            "kind": "Internship",
            "title": "Grafana Labs engineering internship (remote)",
            "org": "Grafana Labs",
            "when": "12 weeks · summer intake, fully remote",
            "opens": None,
            "closes": None,
            "steps": [
                "Watch Grafana Labs' Greenhouse board from autumn — the summer posting goes up there",
                "Pick the track that matches you; the backend and ML tracks are Go-first",
                "Technical screen, then squad interviews — the loop runs in about three weeks",
                "Check the posting lists a country you can legally work from before applying",
            ],
            "city": "Online",
            "tags": ["Paid", "Go", "Backend", "Remote", "Students"],
            "stipend": "Paid: paid internship, rate stated on the posting",
            "source": "Official",
            "url": "https://job-boards.greenhouse.io/grafanalabs",
            "desc": (
                "Grafana Labs — the company behind Grafana, Loki, Mimir and "
                "Tempo — runs a twelve-week, 100% remote summer engineering "
                "internship. Its backend is Go and interns land on a real "
                "squad rather than a sandbox, which makes this the shortest "
                "route from 'I write Go' to 'I shipped Go'. As of late 2026 no "
                "Summer 2027 posting was live yet, so the board is the thing "
                "to watch rather than a page to apply on today; remote-first "
                "also does not mean every country hires, so confirm the "
                "posting's location list first."
            ),
        },
        {
            "kind": "Internship",
            "title": "TechWomen — funded mentorship in the US",
            "org": "U.S. Department of State",
            "when": "~5 weeks in the US · cycle usually opens in January",
            "opens": None,
            "closes": None,
            "steps": [
                "Read the eligibility list on exchanges.state.gov for your country",
                "Apply when the yearly cycle opens — it has historically run Jan–Feb",
                "Shortlisted applicants are interviewed by the local U.S. embassy",
                "Selected mentees travel to the US for mentorship, then give back through a home-country project",
            ],
            "city": "United States",
            "tags": ["Travel covered", "Accommodation", "Mentorship", "Women in tech", "Global"],
            "stipend": (
                "Unpaid + benefits: round-trip airfare, housing, meals and local "
                "transport covered"
            ),
            "source": "Official",
            "url": "https://exchanges.state.gov/non-us/program/techwomen",
            "desc": (
                "A five-week professional mentorship in the United States for "
                "women in STEM from Africa, the Middle East and Asia: a "
                "mentor at a host company, a project, and a professional "
                "network that is otherwise hard to reach from Nairobi. There "
                "is no salary, but airfare, housing, meals and local transport "
                "are covered, which is what makes it affordable. Applicants "
                "need at least two years of professional STEM experience and "
                "strong English; Kenya is on the eligible-country list."
            ),
        },
    ]
)

# Funded placements abroad where the flight and the room are the programme's
# problem, not the applicant's — the HENNGE shape in three more flavours. All
# three touch Go, and each states plainly what it costs to say yes.
INTERNSHIPS.extend(
    [
        {
            "kind": "Internship",
            "title": "Mercari Software Engineer Internship (Tokyo)",
            "org": "Mercari Group",
            "when": "2–3 months · several intakes a year in Tokyo",
            "opens": None,
            "closes": None,
            "steps": [
                "Open careers.mercari.com and pick the engineering internship for your graduating class",
                "Apply with the Go/backend, frontend or mobile experience the posting lists",
                "Interview remotely — overseas candidates get visa and accommodation help",
                "Check the batch dates: Mercari runs more than one intake a year",
            ],
            "city": "Tokyo",
            "tags": ["Paid", "Go", "Backend", "Airfare", "Accommodation", "Japan"],
            "stipend": (
                "Paid: round-trip airfare, accommodation and visa support covered "
                "for overseas interns; intern pay stated on the posting"
            ),
            "source": "Official",
            "url": "https://careers.mercari.com/en/new-graduates/",
            "desc": (
                "Mercari and Merpay run in Tokyo on Go, PHP and Java, and the "
                "engineering internship puts you on the real codebase rather "
                "than a training sandbox — past interns have rewritten "
                "Merpay's settlement service and moved its authentication to "
                "OAuth 2.0. The posting is explicit about the expensive part: "
                "overseas interns get airline tickets arranged and "
                "accommodation sorted, plus visa support, over two to three "
                "months at 20+ hours a week. It targets students who can join "
                "as new graduates in October 2027 or April 2028, so read the "
                "class year before applying."
            ),
        },
        {
            "kind": "Internship",
            "title": "Japan Internship Program (METI)",
            "org": "Government of Japan",
            "when": "~2–3 months in Japan · annual call",
            "opens": None,
            "closes": None,
            "steps": [
                "Check internshipprogram.go.jp for the next call and the eligibility list",
                "Confirm your nationality is on the OECD DAC list — Kenya is",
                "Apply to a host company through the programme, not to the company directly",
                "The in-person course covers airfare, furnished accommodation, insurance and the visa",
            ],
            "city": "Japan",
            "tags": ["Paid", "Travel covered", "Accommodation", "Japan", "Software", "Global"],
            "stipend": (
                "Paid: round-trip economy airfare, furnished accommodation and "
                "utilities, insurance and visa support"
            ),
            "source": "Official",
            "url": "https://internshipprogram.go.jp/english/intern/",
            "desc": (
                "The Japanese government's own internship programme places "
                "foreign professionals with host companies inside Japan and "
                "pays the parts that stop most people: round-trip economy "
                "airfare, accommodation including furniture, bedding and "
                "utilities, travel between the airport, the host company and "
                "the training venue, insurance and the visa. Applicants must "
                "be nationals of an OECD DAC list country living outside "
                "Japan — which covers Kenya — with a degree or equivalent "
                "experience. Host companies span software, IT and "
                "manufacturing, so read the company list rather than assuming "
                "a Go team."
            ),
        },
        {
            "kind": "Internship",
            "title": "Cloudflare Software Engineer Intern (2027)",
            "org": "Cloudflare",
            "when": "12–14 weeks · Winter/Spring or Summer 2027",
            "opens": None,
            "closes": None,
            "steps": [
                "Open the 2027 postings on Cloudflare's Greenhouse board",
                "Pick the office whose work authorization you actually hold — Austin, London or Lisbon",
                "Apply to one term: Winter/Spring (Jan–May) or Summer (May–Sep) 2027",
                "Hiring is rolling until the roles fill, so apply the week the posting opens",
            ],
            "city": "Various",
            "tags": ["Paid", "Go", "Rust", "Systems", "Housing stipend", "On-site"],
            "stipend": (
                "Paid: USD 45–53/hour plus housing and relocation stipends for "
                "US interns; the Lisbon posting lists EUR 2,450/month"
            ),
            "source": "Official",
            "url": "https://job-boards.greenhouse.io/cloudflare",
            "desc": (
                "Cloudflare's edge is written in Go and Rust, and interns ship "
                "code that runs in front of hundreds of millions of people: "
                "12+ weeks in office in Austin, London or Lisbon. US interns "
                "are paid USD 45–53 an hour plus housing and relocation "
                "stipends, which is what makes the summer affordable. The "
                "catch worth reading twice: the postings require existing "
                "authorization to work in that country and do not sponsor "
                "visas, so a Kenyan applicant needs to already be studying or "
                "resident there."
            ),
        },
    ]
)

# Programs are what remains; the moved entries are now served by INTERNSHIPS.
PAID_PROGRAMS = [
    program for program in PAID_PROGRAMS if program["title"] not in _INTERNSHIP_TITLES
]


# ------------------------------------------------- volunteer & speaking slots
#
# No job board carries these. A conference needs a registration desk and a
# line-up, and the only notice is a paragraph on its own site, so they are
# curated server-side for the same reason the paid programs are: adding one is
# a backend change rather than a frontend rebuild.
#
# Compensation is stated the way the organisers state it. Volunteering in tech
# is almost never paid, so the honest form is "Unpaid + benefits:" followed by
# what you actually receive — a free ticket, board and lodging, a crew T-shirt.
# Where the organisers published no perks, say only that it is unpaid.
#
# Dates marked as coming from the previous cycle are estimates: organisers
# announce the next call on their own schedule, and the page tells the reader
# to confirm on the official site.

VOLUNTEER: list[dict] = [
    {
        "kind": "Volunteer",
        "title": "WordCamp Nairobi 2026 — volunteer crew",
        "org": "WordCamp Nairobi",
        "when": "27–28 Nov 2026 · ALX Tech Hub, The Piano, Westlands",
        "opens": None,
        "closes": None,
        # No application window yet, so the card counts down to the event.
        "date": "2026-11-27",
        "steps": [
            "Confirmed but not open yet — the organisers are finalising team roles",
            "Use the 'Get Notified' form on the call page, or watch nairobi.wordcamp.org",
            "Crew shifts run across both days, 27–28 Nov, at the ALX Tech Hub",
            "The speaker application is a separate form if you want a session slot",
        ],
        "city": "Nairobi",
        "tags": [
            "Volunteer",
            "Registration",
            "Speaker",
            "WordPress",
            "On-site",
            "Nairobi",
        ],
        "stipend": "Unpaid + benefits: crew swag and full conference access",
        "source": "Official",
        "url": "https://nairobi.wordcamp.org/2026/organizers/call-for-volunteers/",
        "desc": (
            "Africa's longest-running WordPress conference returns for its "
            "11th edition on 27 and 28 November 2026 at the ALX Tech Hub in "
            "Westlands, with 250+ attendees and 20+ speakers. Volunteers run "
            "event operations and get behind-the-scenes experience, crew swag "
            "and full conference access. One thing to know before relying on "
            "it: the organisers have published the call but not opened it — "
            "the page says it will open shortly and offers to notify you — so "
            "treat this as watch-and-wait rather than apply-today."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "KotlinConf 2027 — volunteer crew",
        "org": "JetBrains",
        "when": "Event 21–23 Apr 2027 · apply by 21 Dec 2026",
        "opens": None,
        "closes": "2026-12-21",
        "steps": [
            "Apply through the volunteer form by 21 Dec 2026",
            "Short online interview during January 2027",
            "Selection results announced 1 Feb 2027",
            "Online orientation Mar–Apr, then an on-site briefing on 20–21 Apr",
            "Work up to 10 hours across the conference days",
        ],
        "city": "Kraków",
        "tags": ["Volunteer", "Registration", "Speaker support", "On-site", "Europe"],
        "stipend": "Unpaid + benefits: complimentary ticket, lunch on event days, uniform",
        "source": "Official",
        "url": "https://kotlinconf.com/volunteers/",
        "desc": (
            "KotlinConf recruits its own crew for registration, speaker "
            "support, navigation and the swag station — up to 10 hours in "
            "total, in exchange for a complimentary ticket for the "
            "conference days (22–23 Apr). Travel and accommodation are yours "
            "to cover, and you must be at least 18."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "Droidcon & Fluttercon Kenya 2027 — speak or crew",
        "org": "Droidcon Kenya",
        "when": "Talk call ~Mar–Jul · event early Nov, Nairobi",
        "opens": "2027-03-01",
        "closes": "2027-07-31",
        "steps": [
            "Call for speakers opens ~Mar on Sessionize",
            "Pick a lightning talk (20 min), session (40 min) or workshop (80 min)",
            "CFP closes ~end of Jul — the 2026 call ran 1 Mar → 31 Jul",
            "Speakers announced ~Sep; the conference runs in early Nov in Nairobi",
            "Ask the organisers for the VISA support letter if you travel in",
        ],
        "city": "Nairobi",
        "tags": ["Speaker", "Volunteer", "Conference", "Mobile", "On-site", "Nairobi"],
        "stipend": "Unpaid + benefits: free speaker ticket (no travel support)",
        "source": "Curated",
        "url": "https://droidcon.co.ke/",
        "desc": (
            "Sub-Saharan Africa's largest mobile developer conference, run "
            "alongside Fluttercon Kenya: two workshop days, 30+ sessions, "
            "50+ speakers. The call for speakers is the door in, and the CFP "
            "text says first-time speakers should not be put off — nobody "
            "needs to be a Kotlin black belt to get a slot. The wider droidcon "
            "family also takes volunteers for registration and speaker "
            "support (roughly eight hours of shifts for a free conference "
            "ticket; volunteer@droidcon.com)."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "PyCon Africa — volunteer crew",
        "org": "PyCon Africa",
        "when": "Annual conference · volunteer call opens with the CFP",
        "opens": None,
        "closes": None,
        "steps": [
            "Watch africa.pycon.org for the next edition's call",
            "Sign up through the Call for Volunteers form",
            "Take a shift: registration, a session room, sprints or socials",
            "Speakers go through the separate call for proposals",
        ],
        "city": "Africa",
        "tags": ["Volunteer", "Speaker", "Python", "Conference", "On-site"],
        "stipend": "Unpaid volunteer crew",
        "source": "Official",
        "url": "https://africa.pycon.org/",
        "desc": (
            "Africa's continental Python conference, hosted by a different "
            "city each year — the 2026 edition ran 7–11 October in Kampala, "
            "Uganda, with a free beginners' day, sprints and a travel-grant "
            "programme. The site keeps a standing Call for Volunteers next to "
            "the call for proposals, which makes the crew the least "
            "intimidating way in for someone who has never spoken."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "Learning Lions — on-site volunteer (2–6 months)",
        "org": "Learning Lions",
        "when": "2–6 months on-site · rolling intakes",
        "opens": None,
        "closes": None,
        "steps": [
            "Read the role list on the volunteer page",
            "Apply through the online form (on-site or remote)",
            "Agree a focus and dates with the programme team",
            "Live and mentor at the ICT centre by Lake Turkana, near Lodwar",
        ],
        "city": "Lodwar",
        "tags": ["Volunteer", "Mentoring", "On-site", "Coding", "Design", "Kenya"],
        "stipend": "Unpaid + benefits: full board and lodging for full-time volunteers",
        "source": "Official",
        "url": "https://www.learninglions.org/volunteer/",
        "desc": (
            "Two to six months mentoring young adults in one of the most "
            "remote parts of Kenya, at the ICT centre near Lodwar in Turkana. "
            "They ask for web developers (React, TypeScript, Node, WordPress), "
            "system and network administrators, database specialists, "
            "designers, project management and sales support, animators, and "
            "hands for campus construction and maintenance. Full-time "
            "volunteers get full board and lodging on campus; part-time "
            "volunteers pay €250 a month for room and board."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "Learning Lions — remote mentor",
        "org": "Learning Lions",
        "when": "Rolling · from anywhere, around your own work",
        "opens": None,
        "closes": None,
        "steps": [
            "Apply through the volunteer form and say you want to join remotely",
            "Agree a mentoring focus with the programme team",
            "Coach one Lion or a small group on real client work",
        ],
        "city": "Online",
        "tags": ["Volunteer", "Mentoring", "Remote", "Coding", "Design"],
        "stipend": "Unpaid remote volunteering",
        "source": "Official",
        "url": "https://www.learninglions.org/volunteer/",
        "desc": (
            "The programme runs a long-standing remote circle alongside the "
            "campus one — one of its graphic-design volunteers has split their "
            "time between the Turkana campus and Cologne since 2018. Freelance "
            "developers and designers are explicitly welcomed, and mentoring "
            "happens against the Lions' real client projects rather than "
            "exercises."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "Inclusive Africa Conference 2027 — volunteer crew",
        "org": "inABLE",
        "when": "Event ~early Jun, Nairobi · call closes ~late May",
        "opens": "2027-04-01",
        "closes": "2027-05-23",
        "steps": [
            "Watch inclusiveafrica.org for the 2027 call for volunteers",
            "Apply before the deadline — the 2026 call closed on 23 May",
            "On-site briefing at the JW Marriott, Nairobi",
        ],
        "city": "Nairobi",
        "tags": ["Volunteer", "Registration", "Accessibility", "On-site", "Nairobi"],
        "stipend": "Unpaid volunteer crew",
        "source": "Curated",
        "url": "https://inclusiveafrica.org/",
        "desc": (
            "Africa's leading digital-accessibility platform, run by inABLE, "
            "which trains blind and visually impaired students across the "
            "continent. The 7th edition ran 2–4 June 2026 at the JW Marriott "
            "in Nairobi, with 300+ delegates on site and thousands more "
            "online, and it is staffed by volunteers on the registration desk "
            "and around the sessions."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "Latitude59xKenya 2026 — volunteer crew",
        "org": "Latitude59",
        "when": "Event 3–4 Dec 2026 · Nairobi",
        "opens": None,
        "closes": None,
        "steps": [
            "Apply through the Latitude59 volunteer form",
            "Take shifts across the two event days in Nairobi",
            "Free entry to the event as a volunteer",
        ],
        "city": "Nairobi",
        "tags": ["Volunteer", "Registration", "Startups", "On-site", "Nairobi"],
        "stipend": "Unpaid + benefits: free entry to the event",
        "source": "Official",
        "url": "https://latitude59.ee/kenya/",
        "desc": (
            "The Estonian startup conference's 4th Nairobi edition, 3–4 "
            "December 2026. Latitude59 has run a dedicated volunteer "
            "programme since 2023, and the call for the Kenya edition is open "
            "to local volunteers — entry is free for the crew."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "DevFest — volunteer crew & speakers",
        "org": "Google Developer Groups",
        "when": "DevFest season Oct–Dec · chapters across Kenya",
        "opens": None,
        "closes": None,
        "steps": [
            "Open a chapter page on gdg.community.dev and join the group",
            "Message the organisers to help run the day",
            "Submit a talk through the chapter's call for speakers",
        ],
        "city": "Kenya",
        "tags": ["Volunteer", "Speaker", "Community", "On-site", "Kenya"],
        "stipend": "Unpaid volunteer crew",
        "source": "Official",
        "url": "https://gdg.community.dev/",
        "desc": (
            "DevFest is the chapter-run event of the Google Developer Groups "
            "year, and it is staffed by its own community: registration desk, "
            "session rooms, A/V, socials. Kenyan chapters in Nairobi, Kisumu, "
            "Eldoret and Kisii each hold one, and the people who volunteer are "
            "usually the ones offered the first-time-speaker slots."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "OSCA Fest — volunteer crew & speakers",
        "org": "Open Source Community Africa",
        "when": "Annual festival · call for speakers ~Nov",
        "opens": None,
        "closes": None,
        "steps": [
            "Follow OSCA for the next festival date and CFP",
            "Submit a talk through the festival's Sessionize call",
            "Volunteer for the crew through the community's channels",
        ],
        "city": "Lagos",
        "tags": ["Volunteer", "Speaker", "Open source", "Community", "On-site"],
        "stipend": "Unpaid volunteer crew",
        "source": "Official",
        "url": "https://festival.oscafrica.org/",
        "desc": (
            "Open Source Festival is OSCA's flagship event — talks, workshops "
            "and hands-on sessions on open-source tooling. The 2025 edition ran "
            "14–16 August in Lagos, and the community also circulates "
            "speaking slots at partner conferences such as the Eclipse "
            "Foundation's Open Community Experience."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "UN Volunteers — national & international assignments",
        "org": "United Nations Volunteers",
        "when": "3–24 months · assignments posted year-round",
        "opens": None,
        "closes": None,
        "steps": [
            "Register a profile on UNV's Unified Volunteering Platform",
            "Filter by duty station (Kenya, or abroad) and by the IT / information management area",
            "Apply to individual assignments — each has its own deadline and host agency",
            "Shortlisted candidates go through the host agency's interview",
        ],
        "city": "Various",
        "tags": ["Volunteer", "Paid", "Allowance", "Accommodation", "Travel", "Global", "Kenya"],
        "stipend": (
            "Paid: monthly living allowance, plus travel, insurance and a "
            "settling-in grant towards accommodation"
        ),
        "source": "Official",
        "url": "https://www.unv.org/become-volunteer",
        "desc": (
            "The only volunteer route that is genuinely funded end to end: a "
            "monthly living allowance, travel entitlements, insurance and — "
            "where the duty station is not within commuting distance — a "
            "settling-in grant and travel reimbursement towards getting there "
            "and living there. National assignments serve inside Kenya with "
            "agencies based in Nairobi; international ones place you with a "
            "host agency abroad. UNV publishes assignments on its own "
            "platform rather than a job board, and 'information and "
            "communications technology' is a standing area, so this is the "
            "way to turn volunteer work into a funded first posting."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "VSO — professional volunteer placements",
        "org": "Voluntary Service Overseas",
        "when": "6–24 months · rolling recruitment",
        "opens": None,
        "closes": None,
        "steps": [
            "Check the current placement list on vsointernational.org",
            "Confirm you meet the bar: a degree or equivalent and about three years' experience",
            "Apply online, then interview with the country programme",
            "Complete the pre-departure training and visa process before you fly",
        ],
        "city": "Kenya",
        "tags": ["Volunteer", "Paid", "Allowance", "Accommodation", "Travel", "Kenya", "Global"],
        "stipend": (
            "Paid: living allowance in local currency, plus accommodation, "
            "travel, insurance and visa costs covered"
        ),
        "source": "Official",
        "url": "https://www.vsointernational.org/volunteering",
        "desc": (
            "VSO has worked in Kenya since 1959 and still places "
            "experienced professionals for six to twenty-four months. For a "
            "placement abroad it covers the living allowance, accommodation, "
            "travel, medical and personal-liability insurance, immunisations "
            "and the visa — the volunteer funds their own medical check and "
            "personal kit. Most placements are in health, education and "
            "livelihoods, with digital and data roles appearing from time to "
            "time, so treat the current list as the filter: this is a route "
            "for someone with a few years behind them, not a first job."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "GopherCon South Africa 2027 — call for speakers",
        "org": "Go South Africa",
        "when": "Event 17–18 Feb 2027 · Johannesburg · CFP open now",
        "opens": None,
        "closes": None,
        "date": "2027-02-17",
        "steps": [
            "Read the call for speakers on gopherconf.co.za",
            "Propose a talk under the 2027 theme, \"Go Beyond — Building the Future with Go\"",
            "Never spoken before? Propose a hands-on or beginner session rather than a keynote",
            "Ask the organisers what travel support exists before you budget for the trip",
        ],
        "city": "Johannesburg",
        "tags": ["Speaker", "Go", "Conference", "On-site", "South Africa"],
        "stipend": (
            "Unpaid + benefits: speaking slot and speaker ticket; travel "
            "support not published, so ask the organisers"
        ),
        "source": "Official",
        "url": "https://gopherconf.co.za/",
        "desc": (
            "South Africa's Go conference returns for a second year on 17–18 "
            "February 2027 in Johannesburg, and the call for speakers is open "
            "now. This is the cheapest way onto a Go stage in Africa: a talk "
            "slot, an audience of Go developers and the recruiters who hire "
            "them, and a line on your CV that no certificate matches. No "
            "travel or accommodation funding is published — ask before you "
            "commit, and treat the trip as yours to fund unless they say "
            "otherwise."
        ),
    },
]

# A conference's call for help usually lives in its own event description, so
# the live calendars are scanned for it. The patterns are deliberately narrow:
# "speaker" on its own matches the line-up of every conference in Africa, so
# only an actual call-to-arms counts.
_VOLUNTEER_CALL_RE = re.compile(
    r"volunteer|call for speakers?|call for (papers|proposals)|"
    r"speaker (applications?|submissions?|slots)|apply to speak|submit a talk|\bcfp\b",
    re.I,
)
_SPEAKER_CALL_RE = re.compile(
    r"call for speakers?|call for (papers|proposals)|"
    r"speaker (applications?|submissions?|slots)|apply to speak|submit a talk|\bcfp\b",
    re.I,
)


# ------------------------------------------------------------- curated events
#
# Conferences with their own ticketing site never reach the TGK/GDG calendars,
# so the big African events are curated here. Every date below was read from
# the official event site — an invented date sends someone to an empty venue.
# Only upcoming entries are served; add a new edition when dates are published.

CURATED_EVENTS: list[dict] = [
    {
        "kind": "Event",
        "title": "Africa Tech Festival 2026",
        "org": "Informa Tech",
        "date": "2026-11-17",
        "when": "17–19 Nov 2026",
        "city": "Cape Town",
        "tags": ["Conference", "Telecom", "Africa"],
        "source": "Curated",
        "url": "https://africatechfestival.com/",
        "stipend": None,
        "desc": (
            "Africa's largest telecom and technology gathering (AfricaCom and "
            "AfricaTech) at the CTICC: operators, investors, startups and "
            "enterprise buyers."
        ),
    },
    {
        "kind": "Event",
        "title": "Africa Tech Summit Nairobi 2027",
        "org": "Africa Tech Summit",
        "date": "2027-02-10",
        "when": "10–11 Feb 2027 · Founders Day 9 Feb",
        "city": "Nairobi",
        "tags": ["Conference", "Startups", "Networking", "Africa"],
        "source": "Curated",
        "url": "https://www.africatechsummit.com/nairobi/",
        "stipend": None,
        "desc": (
            "The 9th Africa Tech Summit at the Sarit Expo Centre connects African "
            "and international startups, investors and corporates, with a "
            "founders-only day before the main conference."
        ),
    },
    {
        "kind": "Event",
        "title": "PyCon Namibia 2027",
        "org": "Python Namibia",
        "date": "2027-02-26",
        "when": "26 Feb–4 Mar 2027",
        "city": "Windhoek",
        "tags": ["Conference", "Python", "Community"],
        "source": "Curated",
        "url": "https://na.pycon.org/2027/",
        "stipend": None,
        "desc": (
            "Namibia's Python conference at NIPAM in Windhoek: tutorials, talks "
            "and sprints, with a strong beginner and open-source focus."
        ),
    },
    {
        "kind": "Event",
        "title": "GITEX Africa 2027",
        "org": "GITEX Africa",
        "date": "2027-04-26",
        "when": "26–28 Apr 2027",
        "city": "Marrakech",
        "tags": ["Expo", "Startups", "AI", "Africa"],
        "source": "Curated",
        "url": "https://gitexafrica.com/",
        "stipend": None,
        "desc": (
            "The continent's largest tech and startup show, at the Palais des "
            "Congrès in Marrakech: exhibitors, government delegations and a "
            "startup pitch programme."
        ),
    },
]


# ------------------------------------------------------------ curated cards
#
# The lists above are the seed. They are copied into `programme_entry` on the
# first boot and every public page reads the table afterwards, so publishing a
# new internship or volunteer slot is a database change, not a deploy.

# Later additions to the built-in set. They carry no guessed deadlines: where
# a programme announces its own dates, `closes` stays None and the card says to
# check the official page, rather than putting an invented date on the page.

PAID_PROGRAMS += [
    {
        "kind": "Paid program",
        "title": "CNCF Mentoring",
        "org": "Cloud Native Computing Foundation",
        "when": "Several cohorts through the year",
        "opens": None,
        "closes": None,
        "steps": [
            "Browse the open projects in the mentoring repo",
            "Apply during a cohort window",
            "Mentored contribution runs for around three months",
        ],
        "city": "Online",
        "tags": ["Open source", "Cloud", "Mentored"],
        "stipend": "Stipend — amount is stated per project",
        "source": "Official",
        "url": "https://github.com/cncf/mentoring",
        "desc": (
            "Mentored, stipended contributions to CNCF projects such as Kubernetes, "
            "Prometheus and Envoy. Cohorts open more than once a year; each listing "
            "states its own stipend and duration."
        ),
    },
    {
        "kind": "Paid program",
        "title": "Season of KDE",
        "org": "KDE",
        "when": "Annual cohort, early in the year",
        "opens": None,
        "closes": None,
        "steps": [
            "Pick a project from the published ideas list",
            "Apply when the next season opens",
            "Work with a mentor through the programme",
        ],
        "city": "Online",
        "tags": ["Open source", "Desktop", "Design"],
        "stipend": "Stipend — see the programme page",
        "source": "Official",
        "url": "https://season.kde.org/",
        "desc": (
            "A mentored open-source programme for KDE, open to code and non-code "
            "contributions such as design, documentation and translation. Dates are "
            "announced each cycle on the Season of KDE site."
        ),
    },
    {
        "kind": "Paid program",
        "title": "Tony Elumelu Foundation Entrepreneurship Programme",
        "org": "Tony Elumelu Foundation",
        "when": "Annual application window, usually early in the year",
        "opens": None,
        "closes": None,
        "steps": [
            "Apply on TEFConnect during the open window",
            "Selected founders join the training and mentoring track",
            "Seed capital is disbursed to successful participants",
        ],
        "city": "Online",
        "tags": ["Entrepreneurship", "Funding", "Africa"],
        "stipend": "USD 5,000 non-refundable seed capital",
        "source": "Official",
        "url": "https://www.tefconnect.net/",
        "desc": (
            "A pan-African entrepreneurship programme combining training, mentoring "
            "and seed capital. Open to founders across Africa; the application window "
            "is announced on TEFConnect each year."
        ),
    },
]

INTERNSHIPS += [
    {
        "kind": "Internship",
        "title": "Safaricom Graduate Management Trainee",
        "org": "Safaricom PLC",
        "when": "Annual intake — watch the careers page",
        "opens": None,
        "closes": None,
        "steps": [
            "Create a profile on the Safaricom careers portal",
            "Apply when the graduate intake opens",
            "Assessment centres and interviews follow",
        ],
        "city": "Nairobi",
        "tags": ["Graduate", "Telecom", "Kenya"],
        "stipend": "Paid graduate role — see listing",
        "source": "Official",
        "url": "https://www.safaricom.co.ke/careers",
        "desc": (
            "Safaricom's graduate track rotates trainees across engineering, data, "
            "commercial and operations teams. Intake dates are published on the "
            "careers portal rather than a fixed calendar."
        ),
    },
    {
        "kind": "Internship",
        "title": "KCB Group Graduate Trainee Programme",
        "org": "KCB Group",
        "when": "Annual intake — check the careers page",
        "opens": None,
        "closes": None,
        "steps": [
            "Apply through the KCB careers portal",
            "Complete the online assessments",
            "Shortlisted candidates attend assessment days",
        ],
        "city": "Nairobi",
        "tags": ["Graduate", "Banking", "Kenya"],
        "stipend": "Paid graduate role — see listing",
        "source": "Official",
        "url": "https://kcbgroup.com/careers/",
        "desc": (
            "A structured graduate programme across KCB's technology, risk and "
            "business functions. Openings are posted to the group careers page when "
            "the intake opens."
        ),
    },
    {
        "kind": "Internship",
        "title": "Equity Group Graduate Trainee Programme",
        "org": "Equity Group Holdings",
        "when": "Annual intake — check the careers page",
        "opens": None,
        "closes": None,
        "steps": [
            "Apply on the Equity Group careers portal",
            "Sit the graduate assessments",
            "Interviews and placements follow",
        ],
        "city": "Nairobi",
        "tags": ["Graduate", "Banking", "Kenya"],
        "stipend": "Paid graduate role — see listing",
        "source": "Official",
        "url": "https://equitygroupholdings.com/ke/careers",
        "desc": (
            "Graduate placements across Equity's banking and technology teams in "
            "Kenya and the wider group. The careers page carries the current intake."
        ),
    },
    {
        "kind": "Internship",
        "title": "Absa Kenya Graduate Programme",
        "org": "Absa Group",
        "when": "Annual intake — check the careers page",
        "opens": None,
        "closes": None,
        "steps": [
            "Search Absa careers for graduate roles in Kenya",
            "Apply to the open programme listing",
            "Assessments and interviews follow",
        ],
        "city": "Nairobi",
        "tags": ["Graduate", "Banking", "Kenya"],
        "stipend": "Paid graduate role — see listing",
        "source": "Official",
        "url": "https://www.absa.africa/careers/",
        "desc": (
            "Absa's graduate programme covers engineering, data, finance and "
            "operations. Listings are posted per country on the group careers site."
        ),
    },
    {
        "kind": "Internship",
        "title": "PwC Kenya Graduate Careers",
        "org": "PwC Kenya",
        "when": "Graduate intake — check the careers page",
        "opens": None,
        "closes": None,
        "steps": [
            "Browse graduate roles on the PwC Kenya careers page",
            "Apply to a specific business area",
            "Online assessment and interviews follow",
        ],
        "city": "Nairobi",
        "tags": ["Graduate", "Consulting", "Kenya"],
        "stipend": "Paid graduate role — see listing",
        "source": "Official",
        "url": "https://www.pwc.co.ke/careers.html",
        "desc": (
            "Graduate and internship roles across PwC Kenya's assurance, tax, "
            "advisory and technology practices. Postings appear on the careers page "
            "through the year."
        ),
    },
    {
        "kind": "Internship",
        "title": "Microsoft Africa Development Centre internships",
        "org": "Microsoft",
        "when": "Internships posted through the year",
        "opens": None,
        "closes": None,
        "steps": [
            "Search Microsoft careers for Nairobi roles",
            "Apply to an internship or student listing",
            "Interviews follow online",
        ],
        "city": "Nairobi",
        "tags": ["Engineering", "Data", "Nairobi"],
        "stipend": "Paid internship — see listing",
        "source": "Official",
        "url": "https://careers.microsoft.com/v2/global/en/home.html",
        "desc": (
            "Engineering and data internships with Microsoft's Africa Development "
            "Centre in Nairobi and Lagos. Roles are listed on the global careers "
            "site as they open."
        ),
    },
]

VOLUNTEER += [
    {
        "kind": "Volunteer",
        "title": "UN Online Volunteering",
        "org": "United Nations Volunteers",
        "when": "Rolling — assignments posted continuously",
        "opens": None,
        "closes": None,
        "steps": [
            "Create a profile on the UNV platform",
            "Filter assignments by skill and remote",
            "Apply to individual postings",
        ],
        "city": "Online",
        "tags": ["Remote", "Online", "Development"],
        "stipend": "Unpaid — volunteer role",
        "source": "Official",
        "url": "https://www.onlinevolunteering.org/",
        "desc": (
            "Remote assignments for UN agencies and NGOs, from translation and "
            "research to web development and data work. Task length and time "
            "commitment vary by posting."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "TED Translators",
        "org": "TED",
        "when": "Rolling — apply any time",
        "opens": None,
        "closes": None,
        "steps": [
            "Apply on the TED Translators site",
            "Complete a short test in your language pair",
            "Subtitle talks once approved",
        ],
        "city": "Online",
        "tags": ["Translation", "Remote", "Media"],
        "stipend": "Unpaid — volunteer role",
        "source": "Official",
        "url": "https://www.ted.com/participate/translate",
        "desc": (
            "Volunteer subtitling and translation of TED talks into and out of "
            "languages including Swahili. Work is self-paced and reviewed by other "
            "volunteers."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "CLEAR Global volunteer translators",
        "org": "CLEAR Global",
        "when": "Rolling — volunteer pool",
        "opens": None,
        "closes": None,
        "steps": [
            "Register through the CLEAR Global volunteer page",
            "State your language pairs and availability",
            "Respond to requests as projects come up",
        ],
        "city": "Online",
        "tags": ["Translation", "Humanitarian", "Remote"],
        "stipend": "Unpaid — volunteer role",
        "source": "Official",
        "url": "https://www.clearglobal.org/volunteer/",
        "desc": (
            "Translation and language support for humanitarian response, including "
            "Swahili and other East African languages. Assignments are sent to the "
            "volunteer pool as projects arise."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "OpenStreetMap Kenya mapping community",
        "org": "OpenStreetMap",
        "when": "Rolling — contribute any time",
        "opens": None,
        "closes": None,
        "steps": [
            "Create an OpenStreetMap account",
            "Read the Kenya mapping wiki and local conventions",
            "Start with map tasks in your area",
        ],
        "city": "Nairobi",
        "tags": ["Mapping", "Open data", "Kenya"],
        "stipend": "Unpaid — volunteer role",
        "source": "Community",
        "url": "https://wiki.openstreetmap.org/wiki/Kenya",
        "desc": (
            "Open mapping of Kenyan roads, amenities and services. Contribution is "
            "continuous and self-directed, with local mapping tasks and community "
            "meet-ups listed on the wiki."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "Mozilla Common Voice",
        "org": "Mozilla",
        "when": "Rolling — contribute any time",
        "opens": None,
        "closes": None,
        "steps": [
            "Open Common Voice and pick a language",
            "Record prompts or validate other recordings",
            "Optionally help with localisation",
        ],
        "city": "Online",
        "tags": ["Voice data", "Open source", "Remote"],
        "stipend": "Unpaid — volunteer role",
        "source": "Official",
        "url": "https://commonvoice.mozilla.org/",
        "desc": (
            "Contribute voice recordings and validation for open speech datasets, "
            "including Swahili. Useful for anyone wanting open-source data and ML "
            "experience on their record."
        ),
    },
    {
        "kind": "Volunteer",
        "title": "Ushahidi open-source contributors",
        "org": "Ushahidi",
        "when": "Rolling — issues labelled for newcomers",
        "opens": None,
        "closes": None,
        "steps": [
            "Read the contributor guide in the repository",
            "Pick an issue labelled for first-time contributors",
            "Open a pull request and work with reviewers",
        ],
        "city": "Online",
        "tags": ["Open source", "Software", "Kenya"],
        "stipend": "Unpaid — volunteer role",
        "source": "Community",
        "url": "https://github.com/ushahidi",
        "desc": (
            "Contribute to the open-source crisis-mapping platform built in Nairobi. "
            "Work is asynchronous on GitHub, with issues tagged for newcomers."
        ),
    },
]

_BUILTIN_CARDS: dict[str, list[dict]] = {
    "Paid program": PAID_PROGRAMS,
    "Internship": INTERNSHIPS,
    "Volunteer": VOLUNTEER,
}
PROGRAMME_KINDS = tuple(_BUILTIN_CARDS)


def deadline_state(item: dict, *, today: date | None = None) -> dict[str, object]:
    """Whether a card's application window has closed, and by how much.

    Computed on the server so the page, the API and any other consumer agree on
    "closed"; a client clock is not allowed to decide whether a deadline has
    passed. Non-ISO values ("Rolling", "TBA") simply carry no deadline.
    """
    today = today or datetime.now(UTC).date()
    closes = str(item.get("closes") or "").strip()[:10]
    try:
        deadline = date.fromisoformat(closes)
    except ValueError:
        return {"expired": False, "days_left": None}
    days_left = (deadline - today).days
    return {"expired": days_left < 0, "days_left": days_left}


def programme_item(entry: ProgrammeEntry) -> dict:
    """One table row in the shape the public pages expect."""
    payload = {
        "kind": entry.kind,
        "title": entry.title,
        "org": entry.org,
        "when": entry.when,
        "opens": entry.opens,
        "closes": entry.closes,
        "date": entry.date or "",
        "steps": list(entry.steps or []),
        "city": entry.city,
        "tags": list(entry.tags or []),
        "stipend": entry.stipend,
        "source": entry.source,
        "url": entry.url,
        "desc": entry.desc,
    }
    return {**payload, **deadline_state(payload)}


def programme_fields(payload: dict) -> dict:
    """Whitelist an incoming dict down to the columns (seed and admin import)."""

    def text(key: str) -> str:
        value = payload.get(key)
        return str(value).strip() if value not in (None, "") else ""

    def optional(key: str) -> str | None:
        value = text(key)
        return value or None

    def strings(key: str) -> list[str]:
        value = payload.get(key) or []
        if isinstance(value, str):
            value = [value]
        return [str(item).strip() for item in value if str(item).strip()]

    return {
        "kind": text("kind"),
        "title": text("title"),
        "org": text("org"),
        "when": text("when"),
        "opens": optional("opens"),
        "closes": optional("closes"),
        "date": optional("date"),
        "steps": strings("steps"),
        "city": text("city"),
        "tags": strings("tags"),
        "stipend": optional("stipend"),
        "source": text("source"),
        "url": text("url"),
        "desc": text("desc"),
    }


def seed_programmes(session: Session) -> int:
    """Insert built-in cards that are missing, preserving edits and tombstones.

    Runs on every boot, not just the first: a card added to the lists has to
    reach deployments that seeded before it existed. A row whose fields differ
    from the constant is an admin edit and is left untouched; a retired row
    still counts as existing, so deleting a built-in card sticks.
    """
    existing = {
        (row.kind, row.title) for row in session.exec(select(ProgrammeEntry)).all()
    }
    inserted = 0
    for cards in _BUILTIN_CARDS.values():
        for card in cards:
            fields = programme_fields(card)
            key = (fields["kind"], fields["title"])
            if not fields["title"] or key in existing:
                continue
            existing.add(key)
            session.add(ProgrammeEntry(**fields))
            inserted += 1
    session.commit()
    return inserted


def curated_cards(kind: str) -> list[dict]:
    """Curated cards of one kind, read from the database.

    Falls back to the built-in lists only while the table is completely empty
    (a fresh database before the first seed). Once seeded, an empty kind stays
    empty: deleting the last internship must not resurrect the constants.
    """
    try:
        with session_scope() as session:
            rows = session.exec(select(ProgrammeEntry)).all()
            if not rows:
                return list(_BUILTIN_CARDS.get(kind, []))
            # Build the dicts while the rows are still attached: the JSON
            # columns are lazy and the session is gone after this block.
            return [
        programme_item(row) for row in rows if row.kind == kind and not row.retired
    ]
    except Exception as exc:  # noqa: BLE001 - a public page must still render
        log.warning("programme table unavailable (%s); serving built-ins", exc)
        return list(_BUILTIN_CARDS.get(kind, []))


def _upcoming_curated_events() -> list[dict]:
    """Curated events that have not started yet, in the timezone they happen in.

    Static data goes stale silently, so past editions drop out automatically
    instead of sitting on the page as a dead link.
    """
    today = datetime.now(EAT).date()
    upcoming: list[dict] = []
    for item in CURATED_EVENTS:
        try:
            start = datetime.fromisoformat(str(item["date"])).date()
        except (KeyError, ValueError):
            continue
        if start >= today:
            upcoming.append({**item, "opens": None, "closes": None, "steps": None})
    return upcoming


# ------------------------------------------------------------------ parsing


@dataclass(slots=True)
class CardEvent:
    """One event as the index page renders it (before its detail page)."""

    url: str
    title: str
    date_text: str = ""     # "Oct 15, 2026" exactly as printed
    location: str = ""      # "Nairobi", "Uasin Gishu", …
    category: str = ""


@dataclass(slots=True)
class DetailEvent:
    """Enrichment from a detail page's schema.org/Event JSON-LD block."""

    description: str = ""
    organizer: str = ""
    start: datetime | None = None
    end: datetime | None = None
    city: str = ""
    online: bool = False
    price: float | None = None
    currency: str = ""


_OPEN_TAG_RE = re.compile(r"<a\s[^>]*>", re.I)
_ATTR_RE = re.compile(r'([\w:.-]+)\s*=\s*"([^"]*)"')
_CATEGORY_RE = re.compile(r'class="event-category"[^>]*>(.*?)</span>', re.S)
_TITLE_RE = re.compile(r'class="event-title"[^>]*>(.*?)</h3>', re.S)
_META_RE = re.compile(r'class="event-meta-row"[^>]*>(.*?)</div>', re.S)
_SPAN_RE = re.compile(r"<span>(.*?)</span>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_LD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I
)
_MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}


def _text(fragment: str) -> str:
    """Strip tags and character references out of an HTML fragment."""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", html_lib.unescape(fragment))).strip()


def parse_card_date(date_text: str) -> str:
    """``"Oct 15, 2026"`` → ``"2026-10-15"`` ("" when unparseable).

    Month names are mapped explicitly rather than via ``strptime`` so the
    parser does not depend on the process locale — a server set to a non-English
    locale would otherwise refuse every English date on the page.
    """
    parts = date_text.replace(",", " ").split()
    if len(parts) != 3:
        return ""
    month_text, day_text, year_text = parts
    month = _MONTHS.get(month_text[:3].lower())
    if month is None:
        return ""
    try:
        day, year = int(day_text), int(year_text)
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return ""


def parse_event_cards(page: str) -> list[CardEvent]:
    """Extract every ``event-card`` anchor from the calendar index page.

    The theme renders whole cards server-side (``data-ssr="1"``) and cards
    contain no nested anchors, so slicing each card at its first ``</a>`` is
    safe; the attribute and field regexes below tolerate attribute reordering.
    """
    cards: list[CardEvent] = []
    for match in _OPEN_TAG_RE.finditer(page):
        if 'class="event-card"' not in match.group(0):
            continue
        end = page.find("</a>", match.end())
        if end == -1:
            continue
        block = page[match.start():end]
        attrs = dict(_ATTR_RE.findall(match.group(0)))
        url = attrs.get("href", "").strip()
        title = _text(_TITLE_RE.search(block).group(1)) if _TITLE_RE.search(block) else ""
        if not url or not title:
            continue  # a half-rendered card is worse than no card
        category = _CATEGORY_RE.search(block)
        meta = _META_RE.search(block)
        spans = [_text(s) for s in _SPAN_RE.findall(meta.group(1))] if meta else []
        cards.append(
            CardEvent(
                url=url,
                title=title,
                date_text=spans[0] if spans else "",
                location=spans[1] if len(spans) > 1 else "",
                category=_text(category.group(1)) if category else "",
            )
        )
    return cards


def _iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_event_detail(page: str) -> DetailEvent | None:
    """Read the schema.org/Event block off an event's detail page."""
    for raw in _LD_RE.findall(page):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("@type") != "Event":
            continue
        detail = DetailEvent(description=_text(str(data.get("description") or "")))
        organizer = data.get("organizer")
        if isinstance(organizer, dict):
            detail.organizer = _text(str(organizer.get("name") or ""))
        elif isinstance(organizer, list) and organizer and isinstance(organizer[0], dict):
            detail.organizer = _text(str(organizer[0].get("name") or ""))
        detail.start = _iso_datetime(data.get("startDate"))
        detail.end = _iso_datetime(data.get("endDate"))
        mode = str(data.get("eventAttendanceMode") or "")
        detail.online = "OnlineEventAttendanceMode" in mode
        location = data.get("location")
        if isinstance(location, dict):
            address = location.get("address")
            city = address.get("addressLocality") if isinstance(address, dict) else ""
            detail.city = _text(str(city or location.get("name") or ""))
        offers = data.get("offers")
        if isinstance(offers, dict):
            price = offers.get("price")
            if isinstance(price, (int, float)) and not isinstance(price, bool):
                detail.price = float(price)
            detail.currency = str(offers.get("priceCurrency") or "")
        return detail
    return None


def _format_when(card: CardEvent, detail: DetailEvent | None) -> tuple[str, str]:
    """Return ``(iso_date, display_when)`` for an event.

    The detail page's ISO start time wins: it carries the year, the timezone
    and the clock time, none of which the card prints. Falls back to the card's
    printed date, then to an honest "to be announced".
    """
    if detail and detail.start:
        start = detail.start
        when = f"{start.day} {start.strftime('%b %Y')}"
        if start.hour or start.minute:
            when += f" · {start.strftime('%H:%M')}"
            if detail.end and detail.end.date() == start.date():
                when += f"–{detail.end.strftime('%H:%M')}"
        return start.date().isoformat(), when
    iso = parse_card_date(card.date_text)
    if iso:
        return iso, card.date_text
    return "", "Date to be announced"


def _format_ticket(detail: DetailEvent | None) -> str | None:
    """Ticket price, so a paid event never reads as free. Free stays silent."""
    if detail is None or detail.price is None or detail.price <= 0:
        return None
    amount = f"{detail.price:,.0f}" if detail.price == int(detail.price) else f"{detail.price:g}"
    return f"Ticket {amount} {detail.currency}".strip()


def _to_item(card: CardEvent, detail: DetailEvent | None) -> dict:
    date_iso, when = _format_when(card, detail)
    if detail and detail.online:
        city = "Online"
    elif detail and detail.city:
        city = detail.city
    else:
        city = card.location or "Kenya"
    return {
        "kind": "Event",
        "title": card.title,
        "org": (detail.organizer if detail and detail.organizer else FALLBACK_ORG),
        "date": date_iso,
        "when": when,
        "city": city,
        "tags": [card.category] if card.category else [],
        "source": SOURCE_NAME,
        "url": card.url,
        "stipend": _format_ticket(detail),
        "opens": None,
        "closes": None,
        "steps": None,
        "desc": detail.description if detail else "",
    }


# ------------------------------------------------------------------- fetch


@dataclass(slots=True)
class Feed:
    """The cached event list plus enough bookkeeping to degrade honestly."""

    items: list[dict] = field(default_factory=list)
    fetched_at: float | None = None      # monotonic, last *successful* fetch
    fetched_at_wall: datetime | None = None
    attempted_at: float = 0.0            # monotonic, last attempt (any outcome)
    ok: bool = False


_lock = asyncio.Lock()
_feed = Feed()
# url → (monotonic stamp, detail-or-None). None means "that page had no usable
# JSON-LD", cached too so a detail-less event is not re-fetched every cycle.
_details: dict[str, tuple[float, DetailEvent | None]] = {}


async def _get(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.text


async def _detail_for(client: httpx.AsyncClient, card: CardEvent) -> DetailEvent | None:
    """Detail page, from cache when possible; a dead page never drops the event."""
    now = time.monotonic()
    stamp, cached = _details.get(card.url, (0.0, None))
    if stamp and now - stamp < (DETAIL_TTL if cached is not None else DETAIL_MISS_TTL):
        return cached
    try:
        detail = parse_event_detail(await _get(client, card.url))
    except Exception as exc:  # noqa: BLE001 - one bad event must not kill the feed
        log.debug("opportunities: detail fetch failed for %s: %s", card.url, exc)
        detail = None
    _details[card.url] = (now, detail)
    return detail


async def _fetch_tgk(client: httpx.AsyncClient) -> list[dict]:
    """One index request plus cached detail pages from the TGK calendar."""
    cards = parse_event_cards(await _get(client, INDEX_URL))[:MAX_EVENTS]
    semaphore = asyncio.Semaphore(ENRICH_CONCURRENCY)

    async def enrich(card: CardEvent) -> DetailEvent | None:
        async with semaphore:
            return await _detail_for(client, card)

    results = await asyncio.gather(*(enrich(card) for card in cards))
    return [
        _to_item(card, detail if isinstance(detail, DetailEvent) else None)
        for card, detail in zip(cards, results, strict=True)
    ]


def _api_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _gdg_item(event: dict, chapter: str, city: str) -> dict | None:
    """One Bevy event-slim row → the same shape as a TGK event."""
    title = str(event.get("title") or "").strip()
    url = str(event.get("url") or event.get("cohost_registration_url") or "").strip()
    start = _api_dt(event.get("start_date"))
    if not title or not url or start is None:
        return None

    start_local = start.astimezone(EAT)
    end = _api_dt(event.get("end_date"))
    end_local = end.astimezone(EAT) if end else None
    when = f"{start_local:%d %b %Y} · {start_local:%H:%M}"
    if end_local and end_local.date() == start_local.date():
        when += f"–{end_local:%H:%M}"

    raw_desc = str(event.get("description_short") or event.get("description") or "")
    desc = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", raw_desc))).strip()
    desc = re.sub(r"\s+([.,;:!?])", r"\1", desc)
    fee = str(event.get("event_type_title") or "").strip()

    return {
        "kind": "Event",
        "title": title,
        "org": chapter,
        "date": start_local.date().isoformat(),
        "when": when,
        "city": city,
        "tags": ["GDG", "Community"],
        "source": GDG_SOURCE,
        "url": url,
        "stipend": fee if "free" in fee.lower() else None,
        "opens": None,
        "closes": None,
        "steps": None,
        "desc": desc[:2000],
    }


async def _fetch_gdg(client: httpx.AsyncClient) -> list[dict]:
    """Upcoming events from the curated GDG chapters' public Bevy API."""
    now = datetime.now(UTC)

    async def one(chapter_id: int, chapter: str, city: str) -> list[dict]:
        try:
            resp = await client.get(
                GDG_API.format(chapter=chapter_id),
                params={"status": "Published", "fields": GDG_FIELDS},
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 - one chapter must not sink the feed
            log.debug("opportunities: GDG chapter %s failed: %s", chapter, exc)
            return []
        items: list[dict] = []
        for event in payload.get("results") or []:
            if not isinstance(event, dict):
                continue
            item = _gdg_item(event, chapter, city)
            start = _api_dt(event.get("start_date"))
            if item and start and start >= now - timedelta(hours=12):
                items.append(item)
        return items

    groups = await asyncio.gather(
        *(one(chapter_id, chapter, city) for chapter_id, (chapter, city) in GDG_CHAPTERS.items())
    )
    return [item for group in groups for item in group]


def _dedupe_events(items: list[dict]) -> list[dict]:
    """TGK wins over a GDG duplicate (it carries the venue detail)."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for item in items:
        key = (str(item.get("title") or "").strip().lower(), str(item.get("date") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


async def _fetch_feed() -> tuple[list[dict], datetime]:
    """Both calendars in parallel, each isolated; stale data survives one outage."""
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        follow_redirects=True,
    ) as client:
        tgk_result, gdg_result = await asyncio.gather(
            _fetch_tgk(client), _fetch_gdg(client), return_exceptions=True
        )

    if isinstance(tgk_result, Exception):
        log.warning("opportunities: TGK feed failed: %s", tgk_result)
        tgk_items = [i for i in _feed.items if i.get("source") == SOURCE_NAME]
    else:
        tgk_items = tgk_result
    if isinstance(gdg_result, Exception):
        log.warning("opportunities: GDG feed failed: %s", gdg_result)
        gdg_items = [i for i in _feed.items if i.get("source") == GDG_SOURCE]
    else:
        gdg_items = gdg_result

    if isinstance(tgk_result, Exception) and isinstance(gdg_result, Exception):
        raise tgk_result

    return _dedupe_events([*tgk_items, *gdg_items]), datetime.now().astimezone()


async def refresh_events(force: bool = False) -> Feed:
    """Refresh the cached event list; never raises.

    Concurrent callers share one fetch (the lock is held across the request),
    so a burst of page loads cannot become a burst of traffic on TGK.
    """
    global _feed
    async with _lock:
        now = time.monotonic()
        if (
            not force
            and _feed.attempted_at
            and now - _feed.attempted_at < CACHE_TTL
        ):
            return _feed
        # Mark the attempt before the network work starts, not only at the
        # end: while a cold fetch is in flight (or was cancelled by the
        # request-side COLD_WAIT cap) the TTL must already be ticking, so
        # that page loads during a TGK outage answer instantly instead of
        # each one restarting a doomed fetch.
        _feed = Feed(
            items=_feed.items,
            fetched_at=_feed.fetched_at,
            fetched_at_wall=_feed.fetched_at_wall,
            attempted_at=now,
            ok=False,
        )
        try:
            items, wall = await _fetch_feed()
        except Exception as exc:  # noqa: BLE001 - a broken feed must not 500
            log.warning("opportunities: event refresh failed: %s", exc)
            stale_ok = _feed.fetched_at is not None and now - _feed.fetched_at < STALE_TTL
            _feed = Feed(
                items=_feed.items if stale_ok else [],
                fetched_at=_feed.fetched_at if stale_ok else None,
                fetched_at_wall=_feed.fetched_at_wall if stale_ok else None,
                attempted_at=now,
                ok=False,
            )
            return _feed
        _feed = Feed(
            items=items,
            fetched_at=time.monotonic(),
            fetched_at_wall=wall,
            attempted_at=time.monotonic(),
            ok=True,
        )
        log.info("opportunities: %d live events from TGK", len(items))
        return _feed


def _sort_key(item: dict) -> float:
    """Nearest deadline first, then dated events, rolling entries last.

    Mirrors the ordering the page shipped with: programs with an application
    window and events with a date interleave by timestamp, and anything
    without a date (rolling cohorts, "updated continuously" calendars) sits at
    the end.
    """
    for key in ("closes", "date"):
        value = item.get(key)
        if value:
            try:
                return datetime.fromisoformat(str(value)).timestamp()
            except ValueError:
                continue
    return 9e15


async def get_opportunities() -> dict:
    """The payload for ``GET /api/public/opportunities``.

    A warm cache answers instantly. A cold one waits at most COLD_WAIT for
    the in-flight fetch, then serves whatever is cached (often just the
    paid programs) with ``events_live=False`` — a stalled third-party feed
    must degrade on the response instead of pinning the page in a loading
    state until the browser or the platform's proxy gives up.
    """
    try:
        feed = await asyncio.wait_for(refresh_events(), timeout=COLD_WAIT)
    except TimeoutError:  # (asyncio.TimeoutError is an alias in 3.12)
        # wait_for cancels the waiter only; the scheduler still refreshes on
        # its own schedule and the next request reuses a warm cache.
        feed = _feed
    # Curated events are static, so they are served even when the live feed is
    # down — a verified conference date does not depend on TGK being reachable.
    items = [*feed.items, *_upcoming_curated_events(), *curated_cards("Paid program")]
    items.sort(key=_sort_key)
    return {
        "items": items,
        "events_live": feed.ok,
        "events_updated_at": feed.fetched_at_wall,
    }


# ------------------------------------------------- live work.ke internships
#
# The curated INTERNSHIPS list can only carry programmes somebody has written
# up. Kenyan employers post internships to work.ke instead, and work.ke exposes
# its whole catalog as one public JSON index that marks each role's seniority —
# so the live collection is a filter over that index rather than a second
# scraper, and it needs no key. The live half is cached, capped, and keeps its
# last good answer through an outage; the curated half never depends on it.

WORKKE_INDEX_URL = "https://work.ke/job-index.json"
WORKKE_TTL = 30 * 60.0        # a tab, not a job board: half an hour is plenty
WORKKE_STALE_TTL = 24 * 3600.0  # an internship older than a day is a dead link
MAX_LIVE_INTERNSHIPS = 80     # the tab stays readable; the index stays cheap


@dataclass(slots=True)
class InternshipFeed:
    """The live internship half, with enough state to report a failed refresh."""

    items: list[dict] = field(default_factory=list)
    fetched_at: float | None = None
    ok: bool = False


_live_feed = InternshipFeed()
_live_lock = asyncio.Lock()


def _is_internship(role: dict) -> bool:
    """True when work.ke marks the row as an internship.

    The index carries seniority ("Internship", "Entry level", …) and an
    employment type; employers fill in one or both, so either counts. Kenyan
    employers also post attachments and graduate/trainee schemes, which are the
    same first-role market under different names.
    """
    seniority = str(role.get("seniority") or "").lower()
    employment = str(role.get("employmentType") or "").lower()
    haystack = f"{seniority} {employment}"
    return any(
        marker in haystack for marker in ("intern", "attachment", "trainee")
    )


def _workke_item(role: dict) -> dict | None:
    """One live internship as the card renders it.

    The index publishes no description — only the fields work.ke's own tile
    shows — so the card points at the listing instead of inventing a summary.
    Compensation is reported as unpublished unless the listing disclosed a
    salary, which the index does tell us.
    """
    title = str(role.get("title") or "").strip()
    employer = str(role.get("employer") or "").strip()
    slug = str(role.get("slug") or "").strip()
    if not title or not employer or not slug:
        return None

    location = str(role.get("location") or "").strip()
    work_mode = str(role.get("workMode") or "").strip()
    remote = work_mode.lower() == "remote" or "remote" in location.lower()

    raw_closing = str(role.get("closingAt") or "").replace("Z", "+00:00")
    closes = None
    when = "Rolling — no closing date published"
    try:
        deadline = datetime.fromisoformat(raw_closing) if raw_closing else None
    except ValueError:
        deadline = None
    if deadline is not None:
        closes = deadline.date().isoformat()
        when = f"Closes {deadline.strftime('%d %b %Y').lstrip('0')}"

    salary_disclosed = bool(role.get("salaryDisclosed"))
    tags = ["Internship", "Kenya", "From work.ke"]
    category = str(role.get("category") or "").strip()
    if category:
        tags.append(category)
    if remote:
        tags.append("Remote")
    if salary_disclosed:
        tags.append("Paid")

    return {
        "kind": "Internship",
        "title": title,
        "org": employer,
        "when": when,
        "opens": None,
        "closes": closes,
        "steps": [
            "Open the work.ke listing from this card",
            "Follow 'View advert' to the employer's own application page",
            "Apply before the closing date printed on the advert",
        ],
        "city": "Online" if remote else (location or "Kenya"),
        "tags": tags,
        "stipend": (
            "Salary disclosed on the listing"
            if salary_disclosed
            else "Stipend not published by the employer"
        ),
        "source": "work.ke",
        "url": f"https://work.ke/jobs/{slug}",
        "desc": (
            f"{title} at {employer} in {location or 'Kenya'}, listed live in "
            "work.ke's Kenyan job index. work.ke links every listing to the "
            "employer's own advert, which carries the full description, the "
            "requirements and the application form."
        ),
    }


async def _fetch_workke_internships() -> list[dict]:
    """The current internship rows from work.ke's JSON index."""
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        response = await client.get(WORKKE_INDEX_URL)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("work.ke index was not a list")

    items = [
        item
        for item in (
            _workke_item(role)
            for role in payload
            if isinstance(role, dict) and _is_internship(role)
        )
        if item is not None
    ]
    items.sort(key=_sort_key)
    return items[:MAX_LIVE_INTERNSHIPS]


async def refresh_workke_internships(force: bool = False) -> InternshipFeed:
    """Refresh the live work.ke internship list; never raises.

    Mirrors ``refresh_events``: the attempt is stamped before the network call
    so a burst of page loads cannot become a burst of traffic on work.ke, and a
    failure keeps the last good list for up to ``WORKKE_STALE_TTL`` rather than
    emptying the tab.
    """
    global _live_feed
    async with _live_lock:
        now = time.monotonic()
        if (
            not force
            and _live_feed.fetched_at is not None
            and now - _live_feed.fetched_at < WORKKE_TTL
        ):
            return _live_feed
        try:
            items = await _fetch_workke_internships()
        except Exception as exc:  # noqa: BLE001 - curated internships still serve
            log.warning("work.ke internships refresh failed: %s", exc)
            stale_ok = (
                _live_feed.fetched_at is not None
                and now - _live_feed.fetched_at < WORKKE_STALE_TTL
            )
            _live_feed = InternshipFeed(
                items=_live_feed.items if stale_ok else [],
                fetched_at=_live_feed.fetched_at if stale_ok else None,
                ok=False,
            )
            return _live_feed
        _live_feed = InternshipFeed(items=items, fetched_at=time.monotonic(), ok=True)
        log.info("work.ke internships: %d live placements", len(items))
        return _live_feed


async def get_internships() -> dict:
    """The payload for ``GET /api/public/internships``.

    Two halves, like the volunteers tab: the curated placements in
    ``INTERNSHIPS`` and the live Kenyan internship collection from work.ke.
    The curated half cannot fail; the live half is capped by ``COLD_WAIT`` so a
    slow index answers with the curated list and ``events_live=False``.
    """
    try:
        feed = await asyncio.wait_for(refresh_workke_internships(), timeout=COLD_WAIT)
    except TimeoutError:  # the scheduled/manual refresh still lands in _live_feed
        feed = _live_feed

    curated = curated_cards("Internship")
    curated_titles = {str(item.get("title") or "").strip().lower() for item in curated}
    live = [
        item
        for item in feed.items
        if str(item.get("title") or "").strip().lower() not in curated_titles
    ]
    items = [*curated, *live]
    # Live work.ke rows carry their own deadline; the same server-side verdict
    # applies so one tab cannot show an expired card as open.
    items = [{**item, **deadline_state(item)} for item in items]
    items.sort(key=_sort_key)
    return {
        "items": items,
        "events_live": feed.ok,
        "events_updated_at": None,
    }


def _volunteer_roles(item: dict) -> list[str]:
    """Tags for an event whose description reads as a call for help."""
    text = f"{item.get('title', '')} {item.get('desc', '')}"
    roles = ["From event feed", "Volunteer"]
    if _SPEAKER_CALL_RE.search(text):
        roles.insert(0, "Speaker")
    return roles


def _from_event_feed(events: list[dict]) -> list[dict]:
    """Upcoming events that are really an ask for volunteers or speakers.

    Re-tagged rather than copied, so a card reads the same as a curated entry
    and the reader can still see which calendar it came from. The event's own
    date is kept as ``date``: a crew call inherits the event it belongs to.
    """
    calls: list[dict] = []
    for item in events:
        text = f"{item.get('title', '')} {item.get('desc', '')}"
        if not _VOLUNTEER_CALL_RE.search(text):
            continue
        calls.append(
            {
                **item,
                "kind": "Volunteer",
                "tags": [*_volunteer_roles(item), *item.get("tags", [])],
            }
        )
    return calls


async def get_volunteers() -> dict:
    """The payload for ``GET /api/public/volunteers``.

    Curated crew and speaking slots, plus whatever the live calendars are
    advertising. The live half reuses the event feed's cache, so this costs no
    extra traffic; when that feed is down the curated entries still serve and
    ``events_live`` says the calendar half is missing.
    """
    try:
        feed = await asyncio.wait_for(refresh_events(), timeout=COLD_WAIT)
    except TimeoutError:
        feed = _feed

    curated = curated_cards("Volunteer")
    curated_titles = {str(item.get("title") or "").strip().lower() for item in curated}
    live = [
        item
        for item in _from_event_feed(feed.items)
        if str(item.get("title") or "").strip().lower() not in curated_titles
    ]
    return {
        "items": sorted([*curated, *live], key=_sort_key),
        "events_live": feed.ok,
        "events_updated_at": feed.fetched_at_wall,
    }
