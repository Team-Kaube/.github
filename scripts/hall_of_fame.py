import json
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

ORG = os.getenv("ORG", "Team-Kaube")
TOKEN = os.environ["GH_TOKEN"]

README_PATH = Path("profile/README.md")

START_MARKER = "<!-- HALL_OF_FAME:START -->"
END_MARKER = "<!-- HALL_OF_FAME:END -->"

API = "https://api.github.com"

# Hall of Fame looks at approximately the last 12 months.
CUTOFF = datetime.now(timezone.utc) - timedelta(days=365)

# Overall scoring system
POINTS_PER_COMMIT = 1
POINTS_PER_MERGED_PR = 5
POINTS_PER_REVIEW = 3
POINTS_PER_100_LINES = 1


# ============================================================
# HELPERS
# ============================================================

def api_request(url, retries=8):
    """
    Make an authenticated GitHub REST API request.

    GitHub's statistics endpoints may return HTTP 202 while
    statistics are being generated. We retry in that case.
    """

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
                body = response.read().decode("utf-8")

                if not body:
                    return None

                return json.loads(body)

        except urllib.error.HTTPError as error:
            if error.code == 202:
                print(
                    f"Statistics still being generated. "
                    f"Retry {attempt + 1}/{retries}: {url}"
                )

                time.sleep(3)
                continue

            try:
                response_body = error.read().decode("utf-8")
            except Exception:
                response_body = ""

            print(
                f"GitHub API error {error.code}\n"
                f"URL: {url}\n"
                f"{response_body}"
            )

            return None

        except urllib.error.URLError as error:
            print(f"Network error: {error}")

            if attempt < retries - 1:
                time.sleep(3)
                continue

            return None

    return None


def paginated(url):
    """
    Yield all objects from a GitHub endpoint using per_page=100.
    """

    page = 1

    while True:
        separator = "&" if "?" in url else "?"

        page_url = (
            f"{url}"
            f"{separator}per_page=100"
            f"&page={page}"
        )

        data = api_request(page_url)

        if not data:
            break

        if not isinstance(data, list):
            print(f"Expected list from {page_url}")
            break

        yield from data

        if len(data) < 100:
            break

        page += 1


def parse_date(value):
    if not value:
        return None

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


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


def format_number(value):
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
# GET ALL ORGANIZATION REPOSITORIES
# ============================================================

print(f"Loading repositories for {ORG}...")

repositories = []

for repo in paginated(
    f"{API}/orgs/{ORG}/repos"
    f"?type=all"
    f"&sort=full_name"
    f"&direction=asc"
):
    # Ignore forks because otherwise commits from upstream
    # projects can distort the Team Kaube leaderboard.
    if repo.get("fork"):
        continue

    # Archived repos are ignored by default.
    if repo.get("archived"):
        continue

    repositories.append(repo)


print(
    f"Found {len(repositories)} accessible repositories "
    f"(public + private + internal where available)."
)


# ============================================================
# COMMIT + CODE STATISTICS
# ============================================================

for index, repo in enumerate(repositories, start=1):
    repo_name = repo["name"]

    visibility = repo.get("visibility")

    if not visibility:
        visibility = (
            "private"
            if repo.get("private")
            else "public"
        )

    print(
        f"[{index}/{len(repositories)}] "
        f"Contributor stats: {repo_name} "
        f"({visibility})"
    )

    contributors = api_request(
        f"{API}/repos/{ORG}/{repo_name}/stats/contributors"
    )

    if not contributors:
        continue

    for contributor in contributors:
        author = contributor.get("author")

        # Deleted GitHub users / anonymous authors may not
        # have an attached account.
        if not author:
            continue

        username = author.get("login")

        if not username or is_bot(username):
            continue

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

            stats[username]["commits"] += week.get("c", 0)
            stats[username]["additions"] += week.get("a", 0)
            stats[username]["deletions"] += week.get("d", 0)


# ============================================================
# PULL REQUESTS + REVIEWS
# ============================================================

for index, repo in enumerate(repositories, start=1):
    repo_name = repo["name"]

    print(
        f"[{index}/{len(repositories)}] "
        f"Pull requests: {repo_name}"
    )

    pull_url = (
        f"{API}/repos/{ORG}/{repo_name}/pulls"
        f"?state=all"
        f"&sort=updated"
        f"&direction=desc"
    )

    for pull in paginated(pull_url):
        updated_at = parse_date(
            pull.get("updated_at")
        )

        # The PR list is sorted by updated_at DESC.
        # Once we reach older PRs we can stop.
        if (
            updated_at
            and updated_at < CUTOFF
        ):
            break

        username = (
            pull.get("user") or {}
        ).get("login")

        merged_at = parse_date(
            pull.get("merged_at")
        )

        if (
            username
            and not is_bot(username)
            and merged_at
            and merged_at >= CUTOFF
        ):
            stats[username]["merged_prs"] += 1

        # ----------------------------------------------------
        # Reviews
        #
        # Count each contributor at most once per PR.
        #
        # Example:
        # APPROVED → COMMENTED → APPROVED
        # still counts as one reviewed PR.
        # ----------------------------------------------------

        reviewers = set()

        reviews_url = (
            f"{API}/repos/{ORG}/{repo_name}"
            f"/pulls/{pull['number']}/reviews"
        )

        for review in paginated(reviews_url):
            reviewer = (
                review.get("user") or {}
            ).get("login")

            submitted_at = parse_date(
                review.get("submitted_at")
            )

            if not reviewer:
                continue

            if is_bot(reviewer):
                continue

            if not submitted_at:
                continue

            if submitted_at < CUTOFF:
                continue

            reviewers.add(reviewer)

        for reviewer in reviewers:
            stats[reviewer]["reviews"] += 1


# ============================================================
# REMOVE EMPTY CONTRIBUTORS
# ============================================================

stats = {
    username: values
    for username, values in stats.items()
    if (
        values["commits"] > 0
        or values["merged_prs"] > 0
        or values["reviews"] > 0
        or values["additions"] > 0
    )
}


# ============================================================
# CALCULATE OVERALL SCORE
# ============================================================

for username, values in stats.items():
    line_points = (
        values["additions"]
        // 100
    )

    values["score"] = (
        values["commits"]
        * POINTS_PER_COMMIT

        + values["merged_prs"]
        * POINTS_PER_MERGED_PR

        + values["reviews"]
        * POINTS_PER_REVIEW

        + line_points
        * POINTS_PER_100_LINES
    )


# ============================================================
# SORT OVERALL LEADERBOARD
# ============================================================

ranking = sorted(
    stats.items(),
    key=lambda item: (
        item[1]["score"],
        item[1]["commits"],
        item[1]["merged_prs"],
    ),
    reverse=True,
)


# ============================================================
# WINNER HELPERS
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
review_user, review_value = winner("reviews")
commit_user, commit_value = winner("commits")


# ============================================================
# BUILD README
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


if ranking:
    for position, (username, values) in enumerate(
        ranking[:3]
    ):
        medal = medals[position]

        lines.append(
            f"{medal} "
            f"{github_profile(username)} "
            f"— **{format_number(values['score'])} pts**"
        )
else:
    lines.append(
        "_No contribution data found yet._"
    )


# ============================================================
# CODE MACHINE
# ============================================================

lines.extend([
    "",
    "### 💻 Code Machine",
    "",
])

if code_user:
    lines.append(
        "Most code contributed: "
        f"**{github_profile(code_user)} "
        f"— +{format_number(code_value)} lines**"
    )
else:
    lines.append("_No data yet._")


# ============================================================
# PR MASTER
# ============================================================

lines.extend([
    "",
    "### 🔀 PR Master",
    "",
])

if pr_user:
    lines.append(
        "Most merged pull requests: "
        f"**{github_profile(pr_user)} "
        f"— {format_number(pr_value)} PRs**"
    )
else:
    lines.append("_No data yet._")


# ============================================================
# REVIEWER
# ============================================================

lines.extend([
    "",
    "### 👀 Reviewer",
    "",
])

if review_user:
    lines.append(
        "Most reviewed pull requests: "
        f"**{github_profile(review_user)} "
        f"— {format_number(review_value)} reviews**"
    )
else:
    lines.append("_No data yet._")


# ============================================================
# COMMIT MACHINE
# ============================================================

lines.extend([
    "",
    "### 🔥 Commit Machine",
    "",
])

if commit_user:
    lines.append(
        "Most commits: "
        f"**{github_profile(commit_user)} "
        f"— {format_number(commit_value)} commits**"
    )
else:
    lines.append("_No data yet._")


# ============================================================
# FULL LEADERBOARD
# ============================================================

lines.extend([
    "",
    "<details>",
    "<summary><strong>📊 Full leaderboard</strong></summary>",
    "",
    "| Rank | Developer | Commits | Merged PRs | Reviews | Lines added | Lines removed | Score |",
    "|---:|---|---:|---:|---:|---:|---:|---:|",
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
        f"| {format_number(values['commits'])} "
        f"| {format_number(values['merged_prs'])} "
        f"| {format_number(values['reviews'])} "
        f"| +{format_number(values['additions'])} "
        f"| -{format_number(values['deletions'])} "
        f"| **{format_number(values['score'])}** |"
    )


lines.extend([
    "",
    "</details>",
    "",
    "<sub>",
    "📅 Based on activity from approximately the last 12 months. "
    "Statistics are aggregated across all Team-Kaube repositories "
    "accessible to the leaderboard bot, including private repositories. "
    "Private repository names, commit messages and PR titles are never "
    "written to this README.",
    "</sub>",
    "",
    "<sub>",
    "🏆 Score: 1 point per commit · "
    "5 points per merged PR · "
    "3 points per reviewed PR · "
    "1 point per 100 added lines",
    "</sub>",
    "",
    END_MARKER,
])


generated_section = "\n".join(lines)


# ============================================================
# UPDATE README
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
        f"README is missing {START_MARKER}"
    )


if END_MARKER not in readme:
    raise RuntimeError(
        f"README is missing {END_MARKER}"
    )


before = readme.split(
    START_MARKER,
    1,
)[0]

after = readme.split(
    END_MARKER,
    1,
)[1]


updated_readme = (
    before
    + generated_section
    + after
)


README_PATH.write_text(
    updated_readme,
    encoding="utf-8",
)


print("")
print("====================================")
print(" Hall of Fame generated successfully")
print("====================================")
print("")

for position, (username, values) in enumerate(
    ranking[:10],
    start=1,
):
    print(
        f"{position}. "
        f"{username}: "
        f"{values['score']} points"
    )