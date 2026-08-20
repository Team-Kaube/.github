import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ============================================================
# CONFIG
# ============================================================

ORG = os.getenv("ORG", "Team-Kaube")
TOKEN = os.environ["GH_TOKEN"]

README_PATH = Path("profile/README.md")

START_MARKER = "<!-- HALL_OF_FAME:START -->"
END_MARKER = "<!-- HALL_OF_FAME:END -->"

API = "https://api.github.com"

DAYS = 365
CUTOFF = datetime.now(timezone.utc) - timedelta(days=DAYS)

POINTS_PER_COMMIT = 1
POINTS_PER_MERGED_PR = 5
POINTS_PER_100_LINES = 1

# Review stats are intentionally disabled in this optimized version.
# Fetching reviews requires many API calls per PR.
INCLUDE_REVIEWS = False


# ============================================================
# HTTP / API HELPERS
# ============================================================

def api_request(url, retries=10):
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Team-Kaube-Hall-of-Fame",
        },
    )

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request) as response:
                status = response.status

                # Contributor statistics may return 202 while GitHub builds them.
                if status == 202:
                    print(
                        f"Stats still generating "
                        f"({attempt + 1}/{retries})"
                    )
                    time.sleep(5)
                    continue

                body = response.read().decode("utf-8")

                if not body:
                    return None

                return json.loads(body)

        except urllib.error.HTTPError as error:
            remaining = error.headers.get("X-RateLimit-Remaining")
            limit = error.headers.get("X-RateLimit-Limit")
            reset = error.headers.get("X-RateLimit-Reset")

            if error.code == 403 and remaining == "0":
                print("")
                print("========================================")
                print(" GITHUB API RATE LIMIT EXCEEDED")
                print("========================================")
                print("")

                if limit:
                    print(f"Limit: {limit}")
                print("Remaining: 0")

                if reset:
                    reset_time = datetime.fromtimestamp(
                        int(reset),
                        tz=timezone.utc,
                    )

                    now = datetime.now(timezone.utc)

                    wait_seconds = max(
                        0,
                        int(
                            (
                                reset_time - now
                            ).total_seconds()
                        ),
                    )

                    print(
                        f"Resets at: {reset_time.isoformat()}"
                    )
                    print(
                        f"Wait: about "
                        f"{wait_seconds // 60} minutes"
                    )

                raise RuntimeError(
                    "GitHub API rate limit exceeded."
                )

            try:
                body = error.read().decode("utf-8")
            except Exception:
                body = ""

            print("")
            print(f"GitHub API error {error.code}")
            print(f"URL: {url}")
            print(body)
            print("")

            return None

        except urllib.error.URLError as error:
            print(f"Network error: {error}")

            if attempt < retries - 1:
                time.sleep(5)
                continue

            return None

    return None


def paginated(url):
    page = 1

    while True:
        separator = "&" if "?" in url else "?"

        page_url = (
            f"{url}"
            f"{separator}per_page=100"
            f"&page={page}"
        )

        data = api_request(page_url)

        if data is None:
            break

        if not isinstance(data, list):
            print(
                f"Expected list response from {page_url}"
            )
            break

        if not data:
            break

        yield from data

        if len(data) < 100:
            break

        page += 1


def is_bot(username):
    if not username:
        return True

    username = username.lower()

    known_bots = {
        "dependabot",
        "dependabot[bot]",
        "github-actions",
        "github-actions[bot]",
        "renovate",
        "renovate[bot]",
    }

    return (
        username.endswith("[bot]")
        or username in known_bots
    )


def github_profile(username):
    return (
        f"[@{username}]"
        f"(https://github.com/{username})"
    )


def number(value):
    return f"{value:,}"


# ============================================================
# STORAGE
# ============================================================

stats = defaultdict(
    lambda: {
        "commits": 0,
        "additions": 0,
        "deletions": 0,
        "merged_prs": 0,
        "reviews": 0,
        "score": 0,
    }
)


# ============================================================
# LOAD ALL ACCESSIBLE ORG REPOS
# ============================================================

print("")
print("========================================")
print(" Team Kaube Hall of Fame")
print("========================================")
print("")

print(f"Organization: {ORG}")
print(f"Period: last {DAYS} days")
print(f"Cutoff: {CUTOFF.isoformat()}")
print("")

repositories = []

repo_url = (
    f"{API}/orgs/{ORG}/repos"
    f"?type=all"
    f"&sort=full_name"
    f"&direction=asc"
)

for repo in paginated(repo_url):
    if repo.get("fork"):
        continue

    if repo.get("archived"):
        continue

    repositories.append(repo)


if not repositories:
    raise RuntimeError(
        "No repositories were returned. "
        "Check PAT access or GitHub API rate limits."
    )


print(
    f"Found {len(repositories)} accessible repositories."
)
print("")

print("Visible repositories:")

for repo in repositories:
    visibility = repo.get("visibility")

    if not visibility:
        visibility = (
            "private"
            if repo.get("private")
            else "public"
        )

    print(
        f" - {repo['name']} ({visibility})"
    )

print("")


# ============================================================
# CONTRIBUTORS / COMMITS / LINES
# ============================================================

print("========================================")
print(" Contributor statistics")
print("========================================")
print("")


for index, repo in enumerate(
    repositories,
    start=1,
):
    repo_name = repo["name"]

    print(
        f"[{index}/{len(repositories)}] "
        f"{repo_name}"
    )

    url = (
        f"{API}/repos/{ORG}/{repo_name}"
        f"/stats/contributors"
    )

    contributors = api_request(url)

    if contributors is None:
        print("  No contributor stats available.")
        continue

    if not isinstance(contributors, list):
        print("  Invalid contributor response.")
        continue

    for contributor in contributors:
        author = contributor.get("author")

        if not author:
            continue

        username = author.get("login")

        if not username or is_bot(username):
            continue

        commits = 0
        additions = 0
        deletions = 0

        for week in contributor.get("weeks", []):
            timestamp = week.get("w")

            if timestamp is None:
                continue

            week_date = datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc,
            )

            if week_date < CUTOFF:
                continue

            commits += week.get("c", 0)
            additions += week.get("a", 0)
            deletions += week.get("d", 0)

        if (
            commits == 0
            and additions == 0
            and deletions == 0
        ):
            continue

        stats[username]["commits"] += commits
        stats[username]["additions"] += additions
        stats[username]["deletions"] += deletions

        print(
            f"  {username}: "
            f"{commits} commits, "
            f"+{additions}, "
            f"-{deletions}"
        )

    print("")


# ============================================================
# MERGED PULL REQUESTS
# ============================================================

print("========================================")
print(" Merged pull requests")
print("========================================")
print("")


cutoff_date = CUTOFF.strftime("%Y-%m-%d")


for username in list(stats.keys()):
    print(f"Checking merged PRs for {username}...")

    query = (
        f"org:{ORG} "
        f"is:pr "
        f"is:merged "
        f"author:{username} "
        f"merged:>={cutoff_date}"
    )

    encoded_query = urllib.parse.quote(query)

    url = (
        f"{API}/search/issues"
        f"?q={encoded_query}"
        f"&per_page=1"
    )

    result = api_request(url)

    if not result:
        continue

    total = result.get(
        "total_count",
        0,
    )

    stats[username]["merged_prs"] = total

    print(
        f"  {username}: {total} merged PRs"
    )


# ============================================================
# REMOVE EMPTY USERS
# ============================================================

stats = {
    username: values
    for username, values in stats.items()
    if (
        values["commits"] > 0
        or values["additions"] > 0
        or values["merged_prs"] > 0
    )
}


if not stats:
    raise RuntimeError(
        "No contributor activity was collected. "
        "The README will not be overwritten."
    )


# ============================================================
# SCORE
# ============================================================

for username, values in stats.items():
    line_points = (
        values["additions"]
        // 100
    )

    values["score"] = (
        values["commits"]
        * POINTS_PER_COMMIT
        +
        values["merged_prs"]
        * POINTS_PER_MERGED_PR
        +
        line_points
        * POINTS_PER_100_LINES
    )


ranking = sorted(
    stats.items(),
    key=lambda item: (
        item[1]["score"],
        item[1]["commits"],
        item[1]["merged_prs"],
        item[1]["additions"],
    ),
    reverse=True,
)


# ============================================================
# CATEGORY WINNERS
# ============================================================

def winner(metric):
    if not stats:
        return None, 0

    username = max(
        stats,
        key=lambda user: stats[user][metric],
    )

    return (
        username,
        stats[username][metric],
    )


code_user, code_value = winner("additions")
pr_user, pr_value = winner("merged_prs")
commit_user, commit_value = winner("commits")


# ============================================================
# CONSOLE OUTPUT
# ============================================================

print("")
print("========================================")
print(" Final leaderboard")
print("========================================")
print("")

for position, (username, values) in enumerate(
    ranking,
    start=1,
):
    print(
        f"{position}. {username} | "
        f"{values['score']} pts | "
        f"{values['commits']} commits | "
        f"{values['merged_prs']} PRs | "
        f"+{values['additions']} lines"
    )


# ============================================================
# GENERATE README
# ============================================================

lines = [
    START_MARKER,
    "",
    "### 👑 Overall",
    "",
]

medals = [
    "🥇",
    "🥈",
    "🥉",
]


for index, (username, values) in enumerate(
    ranking[:3]
):
    lines.append(
        f"{medals[index]} "
        f"{github_profile(username)} "
        f"— **{number(values['score'])} pts**"
    )


# ------------------------------------------------------------
# CODE MACHINE
# ------------------------------------------------------------

lines.extend([
    "",
    "### 💻 Code Machine",
    "",
])

if code_user:
    lines.append(
        "Most code contributed: "
        f"**{github_profile(code_user)} "
        f"— +{number(code_value)} lines**"
    )


# ------------------------------------------------------------
# PR MASTER
# ------------------------------------------------------------

lines.extend([
    "",
    "### 🔀 PR Master",
    "",
])

if pr_user and pr_value > 0:
    lines.append(
        "Most merged pull requests: "
        f"**{github_profile(pr_user)} "
        f"— {number(pr_value)} PRs**"
    )
else:
    lines.append(
        "_No merged pull requests "
        "during this period._"
    )


# ------------------------------------------------------------
# COMMIT MACHINE
# ------------------------------------------------------------

lines.extend([
    "",
    "### 🔥 Commit Machine",
    "",
])

if commit_user:
    lines.append(
        "Most commits: "
        f"**{github_profile(commit_user)} "
        f"— {number(commit_value)} commits**"
    )


# ------------------------------------------------------------
# FULL LEADERBOARD
# ------------------------------------------------------------

lines.extend([
    "",
    "<details>",
    "<summary><strong>📊 Full leaderboard</strong></summary>",
    "",
    "| Rank | Developer | Commits | Merged PRs | Lines added | Lines removed | Score |",
    "|---:|---|---:|---:|---:|---:|---:|",
])


for position, (username, values) in enumerate(
    ranking,
    start=1,
):
    if position == 1:
        rank = "🥇"
    elif position == 2:
        rank = "🥈"
    elif position == 3:
        rank = "🥉"
    else:
        rank = str(position)

    lines.append(
        f"| {rank} "
        f"| {github_profile(username)} "
        f"| {number(values['commits'])} "
        f"| {number(values['merged_prs'])} "
        f"| +{number(values['additions'])} "
        f"| -{number(values['deletions'])} "
        f"| **{number(values['score'])}** |"
    )


lines.extend([
    "",
    "</details>",
    "",
    "<sub>",
    f"📅 Based on activity from the last {DAYS} days. "
    "Statistics are aggregated across all Team-Kaube repositories "
    "accessible to the leaderboard token, including private repositories. "
    "Private repository names, PR titles and commit messages are never "
    "written to this README.",
    "</sub>",
    "",
    "<sub>",
    "🏆 Score: "
    "1 point per commit · "
    "5 points per merged PR · "
    "1 point per 100 added lines",
    "</sub>",
    "",
    END_MARKER,
])


generated = "\n".join(lines)


# ============================================================
# WRITE README
# ============================================================

if not README_PATH.exists():
    raise RuntimeError(
        f"{README_PATH} does not exist."
    )


readme = README_PATH.read_text(
    encoding="utf-8"
)


if START_MARKER not in readme:
    raise RuntimeError(
        f"README missing {START_MARKER}"
    )


if END_MARKER not in readme:
    raise RuntimeError(
        f"README missing {END_MARKER}"
    )


before = readme.split(
    START_MARKER,
    1,
)[0]

after = readme.split(
    END_MARKER,
    1,
)[1]


README_PATH.write_text(
    before + generated + after,
    encoding="utf-8",
)


print("")
print("========================================")
print(" Hall of Fame updated successfully")
print("========================================")
print("")
