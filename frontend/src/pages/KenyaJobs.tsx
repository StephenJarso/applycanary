import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, API_BASE, type EntryLevelRole } from "../api";
import { ErrorBox, Loading, relTime } from "../components";
import { daysUntil, fmtDate } from "../OpportunityCard";

const PAGE_SIZE = 12;

function formatClosing(role: EntryLevelRole): { text: string; urgent: boolean; expired: boolean } {
  if (!role.closing_at) {
    return {
      text: role.closing_label || "Deadline not published by employer",
      urgent: false,
      expired: false,
    };
  }
  const days = daysUntil(role.closing_at);
  if (days < 0) {
    return { text: `Closed ${fmtDate(role.closing_at)}`, urgent: false, expired: true };
  }
  if (days === 0) {
    return { text: "Closes today", urgent: true, expired: false };
  }
  if (days <= 3) {
    return {
      text: `Closes ${fmtDate(role.closing_at)} · ${days} day${days === 1 ? "" : "s"} left`,
      urgent: true,
      expired: false,
    };
  }
  if (days <= 7) {
    return {
      text: `Closes ${fmtDate(role.closing_at)} · ${days} days left`,
      urgent: true,
      expired: false,
    };
  }
  return {
    text: `Closes ${fmtDate(role.closing_at)} · ${days} days left`,
    urgent: false,
    expired: false,
  };
}

export default function KenyaJobs({ embedded = true }: { embedded?: boolean }) {
  const queryClient = useQueryClient();
  const { data, isPending, error } = useQuery({
    queryKey: ["kenya-jobs"],
    queryFn: () => api.kenyaJobs(),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });

  const refresh = useMutation({
    mutationFn: () => api.kenyaJobs(true),
    onSuccess: (feed) => queryClient.setQueryData(["kenya-jobs"], feed),
  });

  // Filter states
  const [search, setSearch] = useState("");
  const [city, setCity] = useState("");
  const [category, setCategory] = useState("");
  const [experience, setExperience] = useState("");
  const [company, setCompany] = useState("");
  const [contractType, setContractType] = useState("");
  const [workArrangement, setWorkArrangement] = useState("");
  const [deadline, setDeadline] = useState("");
  const [salaryDisclosed, setSalaryDisclosed] = useState(false);
  const [includeExpired, setIncludeExpired] = useState(false);
  const [showMoreFilters, setShowMoreFilters] = useState(false);
  const [page, setPage] = useState(1);

  const allItems = data?.roles ?? [];

  // Reset page to 1 whenever filters change
  const handleFilterChange = (setter: (val: string) => void) => (val: string) => {
    setter(val);
    setPage(1);
  };

  // Derive dynamic options from live dataset
  const cities = useMemo(() => {
    const set = new Set<string>();
    for (const r of allItems) {
      if (r.location_labels && r.location_labels.length > 0) {
        for (const label of r.location_labels) {
          if (label && label.toLowerCase() !== "kenya") set.add(label);
        }
      } else if (r.location) {
        const parts = r.location.split(",");
        const primary = (parts[0] || "").trim();
        if (primary && primary.toLowerCase() !== "kenya") set.add(primary);
      }
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [allItems]);

  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const r of allItems) {
      if (r.category) set.add(r.category);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [allItems]);

  const companies = useMemo(() => {
    const set = new Set<string>();
    for (const r of allItems) {
      if (r.employer) set.add(r.employer);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [allItems]);

  const experienceLevels = useMemo(() => {
    const set = new Set<string>();
    for (const r of allItems) {
      if (r.seniority && r.seniority !== "Unknown") set.add(r.seniority);
    }
    const standard = ["Internship", "Entry level", "Mid level", "Senior", "Manager", "Director", "Executive"];
    const found = standard.filter((s) => set.has(s));
    for (const item of set) {
      if (!found.includes(item)) found.push(item);
    }
    return found;
  }, [allItems]);

  const contractTypes = useMemo(() => {
    const set = new Set<string>();
    for (const r of allItems) {
      if (r.employment_type && r.employment_type !== "Unknown") set.add(r.employment_type);
    }
    const standard = ["Full-time", "Contract", "Internship", "Part-time", "Temporary"];
    const found = standard.filter((s) => set.has(s));
    for (const item of set) {
      if (!found.includes(item)) found.push(item);
    }
    return found;
  }, [allItems]);

  const workArrangements = useMemo(() => {
    const set = new Set<string>();
    for (const r of allItems) {
      if (r.work_mode && r.work_mode !== "Unknown") set.add(r.work_mode);
    }
    const standard = ["On-site", "Hybrid", "Remote"];
    const found = standard.filter((s) => set.has(s));
    for (const item of set) {
      if (!found.includes(item)) found.push(item);
    }
    return found;
  }, [allItems]);

  // Filtering
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const now = Date.now();

    return allItems.filter((role) => {
      // Expiration check: jobs whose deadline has passed are excluded unless user explicitly allows
      const isExpired = role.closing_at ? Date.parse(role.closing_at) < now : false;
      if (!includeExpired && isExpired) return false;

      // City filter
      if (city) {
        const inLabels = role.location_labels?.some((l) => l.toLowerCase() === city.toLowerCase());
        const inLoc = role.location.toLowerCase().includes(city.toLowerCase());
        if (!inLabels && !inLoc) return false;
      }

      // Category filter
      if (category && role.category.toLowerCase() !== category.toLowerCase()) {
        return false;
      }

      // Experience level filter
      if (experience && role.seniority.toLowerCase() !== experience.toLowerCase()) {
        return false;
      }

      // Company filter
      if (company && role.employer.toLowerCase() !== company.toLowerCase()) {
        return false;
      }

      // Contract type filter
      if (contractType && role.employment_type?.toLowerCase() !== contractType.toLowerCase()) {
        return false;
      }

      // Work arrangement filter
      if (workArrangement) {
        if (workArrangement.toLowerCase() === "remote") {
          if (!role.is_remote && role.work_mode.toLowerCase() !== "remote") return false;
        } else if (role.work_mode.toLowerCase() !== workArrangement.toLowerCase()) {
          return false;
        }
      }

      // Deadline filter
      if (deadline) {
        if (!role.closing_at) return false;
        const diffMs = Date.parse(role.closing_at) - now;
        const diffDays = Math.ceil(diffMs / (1000 * 60 * 60 * 24));
        if (diffDays < 0) return false;
        if (deadline === "today" && diffDays > 1) return false;
        if (deadline === "3" && diffDays > 3) return false;
        if (deadline === "7" && diffDays > 7) return false;
        if (deadline === "30" && diffDays > 30) return false;
      }

      // Salary filter
      if (salaryDisclosed && !role.salary_disclosed) {
        return false;
      }

      // Text query
      if (q) {
        const haystack = `${role.title} ${role.employer} ${role.location} ${role.category} ${role.seniority} ${role.summary}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }

      return true;
    });
  }, [
    allItems,
    search,
    city,
    category,
    experience,
    company,
    contractType,
    workArrangement,
    deadline,
    salaryDisclosed,
    includeExpired,
  ]);

  // Pagination calculation
  const totalItems = filtered.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const startIdx = (currentPage - 1) * PAGE_SIZE;
  const endIdx = Math.min(startIdx + PAGE_SIZE, totalItems);
  const pageItems = filtered.slice(startIdx, endIdx);

  const activeFiltersCount =
    (city ? 1 : 0) +
    (category ? 1 : 0) +
    (experience ? 1 : 0) +
    (company ? 1 : 0) +
    (contractType ? 1 : 0) +
    (workArrangement ? 1 : 0) +
    (deadline ? 1 : 0) +
    (salaryDisclosed ? 1 : 0) +
    (includeExpired ? 1 : 0) +
    (search.trim() ? 1 : 0);

  const clearAllFilters = () => {
    setSearch("");
    setCity("");
    setCategory("");
    setExperience("");
    setCompany("");
    setContractType("");
    setWorkArrangement("");
    setDeadline("");
    setSalaryDisclosed(false);
    setIncludeExpired(false);
    setPage(1);
  };

  const scrollToResults = () => {
    const el = document.getElementById("kenya-job-results");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const goToPage = (p: number) => {
    setPage(p);
    scrollToResults();
  };

  // Generate page numbers for pagination
  const pageNumbers = useMemo(() => {
    const pages: (number | "...")[] = [];
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i);
    } else {
      pages.push(1);
      if (currentPage > 3) pages.push("...");
      const start = Math.max(2, currentPage - 1);
      const end = Math.min(totalPages - 1, currentPage + 1);
      for (let i = start; i <= end; i++) pages.push(i);
      if (currentPage < totalPages - 2) pages.push("...");
      pages.push(totalPages);
    }
    return pages;
  }, [totalPages, currentPage]);

  return (
    <div className={`opp-page${embedded ? " opp-embedded" : ""}`}>
      {/* Page Header */}
      <div className="page-intro" id="kenya-job-results">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
          <div>
            <h2 className="page-title">Kenya Jobs</h2>
            <p className="page-sub">
              Live Kenya job openings from{" "}
              <a href="https://work.ke/jobs" target="_blank" rel="noreferrer">
                work.ke
              </a>{" "}
              with official employer application links, transparent deadlines and zero application fees.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
            title="Fetch latest jobs live from work.ke"
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
          >
            {refresh.isPending ? (
              <span className="spinner" />
            ) : (
              <span className="material-symbols-outlined" style={{ fontSize: 18 }} aria-hidden="true">
                sync
              </span>
            )}
            Refresh
          </button>
        </div>
      </div>

      {/* Main Container with Top Filters */}
      <div className="kenya-jobs-container" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        {/* Search & Top Filter Bar */}
        <div className="kenya-filter-panel" style={{
          background: "var(--ac-surface-container-low, #f8fafc)",
          border: "1px solid var(--border, #e2e8f0)",
          borderRadius: "12px",
          padding: "18px 20px",
          display: "flex",
          flexDirection: "column",
          gap: 16
        }}>
          {/* Search Input Row */}
          <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <div style={{ position: "relative", flex: "1 1 300px" }}>
              <span
                className="material-symbols-outlined"
                style={{
                  position: "absolute",
                  left: 12,
                  top: "50%",
                  transform: "translateY(-50%)",
                  color: "var(--text-dim, #64748b)",
                  pointerEvents: "none",
                  fontSize: 20,
                }}
                aria-hidden="true"
              >
                search
              </span>
              <input
                type="search"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                  setPage(1);
                }}
                placeholder="Role, employer, city or keywords…"
                aria-label="Search roles, employer or keywords"
                style={{
                  width: "100%",
                  paddingLeft: 40,
                  height: 42,
                  borderRadius: 8,
                  border: "1px solid var(--border, #cbd5e1)",
                  background: "var(--bg, #fff)",
                  color: "var(--text, #0f172a)",
                  fontSize: 14,
                }}
              />
            </div>

            <button
              type="button"
              className={`opp-chip ${salaryDisclosed ? "opp-chip-active" : ""}`}
              aria-pressed={salaryDisclosed}
              onClick={() => {
                setSalaryDisclosed(!salaryDisclosed);
                setPage(1);
              }}
              style={{
                height: 42,
                padding: "0 14px",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                fontWeight: 600,
                fontSize: 13,
                cursor: "pointer",
                borderRadius: 8,
                border: salaryDisclosed ? "1px solid var(--ac-primary, #0284c7)" : "1px solid var(--border, #cbd5e1)",
                background: salaryDisclosed ? "var(--ac-primary-container, #e0f2fe)" : "var(--bg, #fff)",
                color: salaryDisclosed ? "var(--ac-on-primary-container, #0369a1)" : "var(--text, #334155)",
              }}
            >
              💰 Salary disclosed
            </button>

            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setShowMoreFilters(!showMoreFilters)}
              style={{ height: 42, display: "inline-flex", alignItems: "center", gap: 6, fontWeight: 600 }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 18 }} aria-hidden="true">
                {showMoreFilters ? "expand_less" : "tune"}
              </span>
              {showMoreFilters ? "Fewer filters" : "More filters"}
              {activeFiltersCount > 0 && !showMoreFilters && (
                <span
                  style={{
                    background: "var(--ac-primary, #0284c7)",
                    color: "#fff",
                    borderRadius: "50%",
                    width: 20,
                    height: 20,
                    fontSize: 11,
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  {activeFiltersCount}
                </span>
              )}
            </button>

            {activeFiltersCount > 0 && (
              <button
                type="button"
                className="opp-clear"
                onClick={clearAllFilters}
                style={{ height: 42, padding: "0 10px", fontSize: 13 }}
              >
                Clear all
              </button>
            )}
          </div>

          {/* Primary Filters Grid */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: 12,
            }}
          >
            {/* City or Area */}
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim, #64748b)" }}>
                City or area
              </label>
              <select
                value={city}
                onChange={(e) => handleFilterChange(setCity)(e.target.value)}
                style={{
                  height: 38,
                  padding: "0 10px",
                  borderRadius: 6,
                  border: "1px solid var(--border, #cbd5e1)",
                  background: "var(--bg, #fff)",
                  color: "var(--text, #0f172a)",
                  fontSize: 13,
                }}
              >
                <option value="">All cities</option>
                {cities.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>

            {/* Category */}
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim, #64748b)" }}>
                Category
              </label>
              <select
                value={category}
                onChange={(e) => handleFilterChange(setCategory)(e.target.value)}
                style={{
                  height: 38,
                  padding: "0 10px",
                  borderRadius: 6,
                  border: "1px solid var(--border, #cbd5e1)",
                  background: "var(--bg, #fff)",
                  color: "var(--text, #0f172a)",
                  fontSize: 13,
                }}
              >
                <option value="">All categories</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>

            {/* Experience Level */}
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim, #64748b)" }}>
                Experience level
              </label>
              <select
                value={experience}
                onChange={(e) => handleFilterChange(setExperience)(e.target.value)}
                style={{
                  height: 38,
                  padding: "0 10px",
                  borderRadius: 6,
                  border: "1px solid var(--border, #cbd5e1)",
                  background: "var(--bg, #fff)",
                  color: "var(--text, #0f172a)",
                  fontSize: 13,
                }}
              >
                <option value="">All experience levels</option>
                {experienceLevels.map((exp) => (
                  <option key={exp} value={exp}>
                    {exp}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Secondary Filters (Expandable) */}
          {showMoreFilters && (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                gap: 12,
                paddingTop: 12,
                borderTop: "1px dashed var(--border, #e2e8f0)",
              }}
            >
              {/* Company */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim, #64748b)" }}>
                  Company
                </label>
                <select
                  value={company}
                  onChange={(e) => handleFilterChange(setCompany)(e.target.value)}
                  style={{
                    height: 38,
                    padding: "0 10px",
                    borderRadius: 6,
                    border: "1px solid var(--border, #cbd5e1)",
                    background: "var(--bg, #fff)",
                    color: "var(--text, #0f172a)",
                    fontSize: 13,
                  }}
                >
                  <option value="">All employers</option>
                  {companies.map((comp) => (
                    <option key={comp} value={comp}>
                      {comp}
                    </option>
                  ))}
                </select>
              </div>

              {/* Contract Type */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim, #64748b)" }}>
                  Contract type
                </label>
                <select
                  value={contractType}
                  onChange={(e) => handleFilterChange(setContractType)(e.target.value)}
                  style={{
                    height: 38,
                    padding: "0 10px",
                    borderRadius: 6,
                    border: "1px solid var(--border, #cbd5e1)",
                    background: "var(--bg, #fff)",
                    color: "var(--text, #0f172a)",
                    fontSize: 13,
                  }}
                >
                  <option value="">All employment types</option>
                  {contractTypes.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>

              {/* Work Arrangement */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim, #64748b)" }}>
                  Work arrangement
                </label>
                <select
                  value={workArrangement}
                  onChange={(e) => handleFilterChange(setWorkArrangement)(e.target.value)}
                  style={{
                    height: 38,
                    padding: "0 10px",
                    borderRadius: 6,
                    border: "1px solid var(--border, #cbd5e1)",
                    background: "var(--bg, #fff)",
                    color: "var(--text, #0f172a)",
                    fontSize: 13,
                  }}
                >
                  <option value="">Any work arrangement</option>
                  {workArrangements.map((mode) => (
                    <option key={mode} value={mode}>
                      {mode}
                    </option>
                  ))}
                </select>
              </div>

              {/* Deadline */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-dim, #64748b)" }}>
                  Deadline
                </label>
                <select
                  value={deadline}
                  onChange={(e) => handleFilterChange(setDeadline)(e.target.value)}
                  style={{
                    height: 38,
                    padding: "0 10px",
                    borderRadius: 6,
                    border: "1px solid var(--border, #cbd5e1)",
                    background: "var(--bg, #fff)",
                    color: "var(--text, #0f172a)",
                    fontSize: 13,
                  }}
                >
                  <option value="">Any open deadline</option>
                  <option value="today">Closing today</option>
                  <option value="3">Closing in 3 days</option>
                  <option value="7">Closing in 7 days</option>
                  <option value="30">Closing in 30 days</option>
                </select>
              </div>

              {/* Expired Jobs Toggle */}
              <div style={{ display: "flex", alignItems: "flex-end", paddingBottom: 4 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer", userSelect: "none" }}>
                  <input
                    type="checkbox"
                    checked={includeExpired}
                    onChange={(e) => {
                      setIncludeExpired(e.target.checked);
                      setPage(1);
                    }}
                  />
                  <span>Include closed/expired roles</span>
                </label>
              </div>
            </div>
          )}
        </div>

        {/* Live Feed Status & Count Bar */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
          <p className="opp-count" style={{ fontSize: 14 }}>
            <strong>{totalItems}</strong> open role{totalItems === 1 ? "" : "s"}
            {totalItems > 0 && (
              <span style={{ color: "var(--text-dim, #64748b)" }}>
                {" "}· Showing {startIdx + 1}–{endIdx}
              </span>
            )}
            {data?.fetched_at && (
              <span style={{ color: "var(--text-faint, #94a3b8)", fontSize: 12, marginLeft: 8 }}>
                (Live · synced {relTime(data.fetched_at)} ago)
              </span>
            )}
          </p>

          {totalPages > 1 && (
            <p style={{ margin: 0, fontSize: 13, color: "var(--text-dim, #64748b)" }}>
              Page {currentPage} of {totalPages}
            </p>
          )}
        </div>

        {/* Loading / Errors */}
        {isPending && <Loading label="Loading Kenyan vacancies from work.ke…" />}
        {error && <ErrorBox error={error} />}

        {refresh.isError && (
          <div className="banner banner-bad" role="alert">
            {String(refresh.error)}
          </div>
        )}

        {/* Empty State */}
        {!isPending && totalItems === 0 && (
          <div className="opp-empty" style={{ padding: "40px 20px", textAlign: "center" }}>
            <p style={{ fontSize: 16, fontWeight: 600, margin: "0 0 8px" }}>No roles match your filters.</p>
            <p style={{ fontSize: 14, color: "var(--text-dim, #64748b)", margin: "0 0 16px" }}>
              Try adjusting your city, experience level or clearing all filters.
            </p>
            {activeFiltersCount > 0 && (
              <button type="button" className="btn btn-primary btn-sm" onClick={clearAllFilters}>
                Clear filters
              </button>
            )}
          </div>
        )}

        {/* Job Cards Grid */}
        {!isPending && totalItems > 0 && (
          <div className="opp-grid">
            {pageItems.map((role) => {
              const closing = formatClosing(role);
              // The live feed publishes work.ke slugs, not the employer's advert,
              // so those open through the API, which redirects to the posting.
              // Roles that already carry an external advert link straight to it.
              const applyTarget =
                role.apply_url && role.apply_url !== role.source_url
                  ? role.apply_url
                  : `${API_BASE}/api/public/kenya-jobs/${encodeURIComponent(role.slug)}/apply`;

              return (
                <article
                  key={role.slug}
                  className="opp-card"
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "space-between",
                    opacity: closing.expired ? 0.75 : 1,
                  }}
                >
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {/* Top Badges */}
                    <div className="opp-card-top" style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
                      <span className="opp-kind opp-kind-intern" style={{ fontSize: 11, padding: "2px 8px" }}>
                        {role.seniority || "All levels"}
                      </span>

                      {role.employer_type && (
                        <span className="opp-source" style={{ fontSize: 12 }}>
                          {role.employer_type}
                          {role.agency ? ` · ${role.agency}` : ""}
                        </span>
                      )}

                      {role.work_mode && (
                        <span
                          style={{
                            fontSize: 11,
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: "var(--bg-hover, #f1f5f9)",
                            color: "var(--text-dim, #475569)",
                            border: "1px solid var(--border, #cbd5e1)",
                          }}
                        >
                          {role.work_mode}
                        </span>
                      )}

                      {role.salary_disclosed && (
                        <span
                          style={{
                            fontSize: 11,
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: "#dcfce7",
                            color: "#166534",
                            fontWeight: 600,
                          }}
                        >
                          Salary disclosed
                        </span>
                      )}
                    </div>

                    {/* Title & Employer / Location */}
                    <div>
                      <h3 style={{ fontSize: 16, lineHeight: 1.35, margin: "0 0 6px" }}>{role.title}</h3>
                      <p className="opp-meta" style={{ fontSize: 13, color: "var(--text-dim, #64748b)", margin: 0 }}>
                        <strong>{role.employer}</strong> · {role.location}
                        {role.employment_type && ` · ${role.employment_type}`}
                      </p>
                    </div>

                    {/* Summary if available */}
                    {role.summary && (
                      <p className="opp-desc entry-summary" style={{ fontSize: 13, color: "var(--text, #334155)" }}>
                        {role.summary}
                      </p>
                    )}
                  </div>

                  {/* Footer / Actions */}
                  <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
                    {/* Deadline indicator */}
                    <div
                      className={`opp-timeline ${closing.urgent ? "entry-deadline-soon" : ""}`}
                      style={{
                        padding: "6px 10px",
                        borderRadius: 6,
                        fontSize: 12,
                        background: closing.urgent
                          ? "var(--bad-bg, #fef2f2)"
                          : closing.expired
                          ? "var(--bg-hover, #f1f5f9)"
                          : "var(--ac-surface-container-low, #f8fafc)",
                        border: closing.urgent
                          ? "1px solid var(--bad, #ef4444)"
                          : "1px solid var(--border, #e2e8f0)",
                        color: closing.urgent
                          ? "var(--bad, #dc2626)"
                          : closing.expired
                          ? "var(--text-faint, #94a3b8)"
                          : "var(--text-dim, #475569)",
                      }}
                    >
                      <p style={{ margin: 0, fontWeight: closing.urgent ? 600 : 400 }}>
                        📅 {closing.text}
                      </p>
                    </div>

                    {/* Tags & Action Button */}
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                      <span
                        style={{
                          fontSize: 12,
                          color: "var(--text-dim, #64748b)",
                          background: "var(--bg-hover, #f1f5f9)",
                          padding: "2px 8px",
                          borderRadius: 4,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          maxWidth: 180,
                        }}
                      >
                        {role.category || "General"}
                      </span>

                      <a
                        href={applyTarget}
                        target="_blank"
                        rel="noreferrer"
                        className="btn btn-primary btn-sm"
                        style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4 }}
                      >
                        View role ↗
                      </a>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}

        {/* Pagination Controls */}
        {!isPending && totalPages > 1 && (
          <nav
            aria-label="Jobs pagination"
            style={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              gap: 8,
              padding: "24px 0",
              flexWrap: "wrap",
            }}
          >
            {/* Previous Button */}
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={currentPage <= 1}
              onClick={() => goToPage(currentPage - 1)}
              style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 18 }} aria-hidden="true">
                arrow_back
              </span>
              Previous
            </button>

            {/* Page Numbers */}
            <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
              {pageNumbers.map((p, idx) =>
                p === "..." ? (
                  <span key={`dots-${idx}`} style={{ padding: "0 6px", color: "var(--text-dim, #64748b)" }}>
                    …
                  </span>
                ) : (
                  <button
                    key={p}
                    type="button"
                    onClick={() => goToPage(p)}
                    aria-current={p === currentPage ? "page" : undefined}
                    style={{
                      minWidth: 36,
                      height: 36,
                      padding: "0 8px",
                      borderRadius: 6,
                      border: p === currentPage ? "1px solid var(--ac-primary, #0284c7)" : "1px solid var(--border, #cbd5e1)",
                      background: p === currentPage ? "var(--ac-primary, #0284c7)" : "var(--bg, #fff)",
                      color: p === currentPage ? "#fff" : "var(--text, #334155)",
                      fontWeight: p === currentPage ? 700 : 500,
                      fontSize: 13,
                      cursor: "pointer",
                    }}
                  >
                    {p}
                  </button>
                )
              )}
            </div>

            {/* Next Button */}
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={currentPage >= totalPages}
              onClick={() => goToPage(currentPage + 1)}
              style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
            >
              Next
              <span className="material-symbols-outlined" style={{ fontSize: 18 }} aria-hidden="true">
                arrow_forward
              </span>
            </button>
          </nav>
        )}
      </div>
    </div>
  );
}
