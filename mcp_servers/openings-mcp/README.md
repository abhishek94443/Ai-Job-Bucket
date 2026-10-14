# openings-mcp (Job Bucket Discovery Engine)

This directory vendors the open-source `openings-mcp` service (MIT License) as the native job discovery engine for Job Bucket.

## Architecture and Prebuilt Binaries

Job Bucket ships with precompiled binaries under `bin/`:
- `bin/openings-mcp-windows-amd64.exe` (Windows x64)
- `bin/openings-mcp-linux-amd64` (Linux x86_64)
- `bin/openings-mcp-darwin-arm64` (macOS Apple Silicon)
- `bin/openings-mcp-darwin-amd64` (macOS Intel)

The Job Bucket Python backend dynamically detects the host operating system and resolves the corresponding executable in `src/mcp_client.py` without requiring Go to be installed on deployment environments.

### Customization and Extensions

The full Go source code is included so developers can customize scrapers or add new job boards and career portals:
- Server entry point: `cmd/openings-mcp/`
- Provider implementations: `internal/provider/`
- Recompilation:
  `go build -ldflags="-s -w" -o bin/openings-mcp-<os>-<arch> ./cmd/openings-mcp`

---

## Overview

**openings-mcp** searches job listings across job boards and company career sites from any MCP client (Claude Code, Codex, Gemini CLI, Job Bucket, and others).

- **Job boards**: [104](https://www.104.com.tw), [Cake](https://www.cake.me), [Jobindex](https://www.jobindex.dk) (Denmark), [マイナビ転職](https://tenshoku.mynavi.jp) (Japan), [Flowxtra](https://flowxtra.com) (board-wide across every company on the Flowxtra ATS), [freehire.me](https://freehire.me) (IT/tech catalogue across company ATS boards), [LinkedIn](https://www.linkedin.com), and [Indeed](https://www.indeed.com) (public search).
- **Company career sites**: 41,000+ companies hosted on Workday, Ashby, Greenhouse, Lever, Teamtailor, Recruitee, Eightfold, SAP SuccessFactors, SmartRecruiters, Workable, Rippling, iCIMS, Oracle Recruiting Cloud, JOIN, UKG Pro (UltiPro), Dayforce, Avature, HERP Hire, engage, HRMOS, MokaHR, BambooHR, and ADP MyJobs ATS platforms.
- **Dedicated company career sites**: Amazon Jobs, Apple Careers, Google Careers, Meta Careers, and TSMC Careers.

Note: Token use adds up when exploring multiple postings or fetching full job details.

> **Disclaimer:** This is an unofficial, personal-use tool. It is not affiliated with, endorsed by, or sponsored by 104 Corporation, TSMC, or any other job board or company whose listings it searches. It calls public web endpoints; please respect terms of service and use it at a reasonable, low frequency. The project ships no scraped job data.

## Standalone Installation

With Homebrew:
```
brew install --cask amikai/tap/openings-mcp
```

With Go:
```
go install github.com/amikai/openings-mcp/cmd/openings-mcp@latest
```

With Docker:
```
docker pull ghcr.io/amikai/openings-mcp
```

## Adding to Other MCP Clients

With `openings-mcp` on your PATH:

**Claude Code**
```
claude mcp add --scope user openings-mcp -- openings-mcp
```

**Codex**
```
codex mcp add openings-mcp -- openings-mcp
```

**Gemini CLI**
```
gemini mcp add openings-mcp openings-mcp
```

## Disclaimer

This is an unofficial tool. It is not affiliated with, endorsed by, or supported by 104 Corporation, TSMC, or any other job board or company whose listings it searches.

Some providers rely on undocumented APIs that may change or stop working at any time without notice. Job listing data belongs to the respective sites. The server fetches it on your behalf when you invoke a tool and stores or redistributes nothing.

Use this tool for personal job searching at a human pace. Every request goes out from your own machine, and a site may throttle or temporarily block your IP if too many arrive too quickly. The server does not rate-limit requests.

## Credits

- [freehire](https://github.com/strelov1/freehire)

Inspired by these MCP job-search servers:
- [job104-mcp](https://github.com/mozzan/job104-mcp)
- [jobspy-mcp-server](https://github.com/borgius/jobspy-mcp-server)
- [mcp-jobs](https://github.com/mergedao/mcp-jobs)
- [104-mcp-server](https://github.com/bigbrainw/104-mcp-server)

Company roster data cross-referenced from:
- [OpenPostings](https://github.com/Masterjx9/OpenPostings) (company/ATS discovery data used to expand the curated roster)

Jobindex search reference:
- [ai-job-search](https://github.com/MadsLorentzen/ai-job-search)

## License

[MIT](LICENSE)
