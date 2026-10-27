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
from datetime import UTC, datetime, timedelta, timezone

import httpx

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
    items = [*feed.items, *_upcoming_curated_events(), *PAID_PROGRAMS]
    items.sort(key=_sort_key)
    return {
        "items": items,
        "events_live": feed.ok,
        "events_updated_at": feed.fetched_at_wall,
    }


def get_internships() -> dict:
    """The payload for ``GET /api/public/internships``.

    Internships are curated, so this is synchronous and cannot fail the way the
    live event feed can. The response reuses the opportunities shape so the
    frontend can share one card component.
    """
    return {
        "items": sorted(INTERNSHIPS, key=_sort_key),
        "events_live": True,
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

    curated_titles = {str(item.get("title") or "").strip().lower() for item in VOLUNTEER}
    live = [
        item
        for item in _from_event_feed(feed.items)
        if str(item.get("title") or "").strip().lower() not in curated_titles
    ]
    return {
        "items": sorted([*VOLUNTEER, *live], key=_sort_key),
        "events_live": feed.ok,
        "events_updated_at": feed.fetched_at_wall,
    }
