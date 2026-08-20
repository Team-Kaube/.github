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

# Look at approximately the last 12 months
CUTOFF = datetime.now(timezone.utc) - timedelta(days=365)

# Overall score
POINTS_PER_COMMIT = 1
POINTS_PER_MERGED_PR = 5
POINTS_PER_REVIEW = 3
POINTS_PER_100_LINES = 1


# ============================================================
# HELPERS
# ============================================================

def api_request(url, retries=10):
    """
    Make an authenticated request to the GitHub REST API.

    GitHub statistics endpoints can return HTTP 202 while
    statistics are being generated. In that case we retry.
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
                status = response.status

                # GitHub contributor statistics may need time
                # to be generated.
                if status == 202:
                    print(
                        f"Statistics still being generated "
                        f"({attempt + 1}/{retries})"
                    )
                    print(f"URL: {url}")

                    time.sleep(5)
                    continue

                body = response.read().decode("utf-8")

                if not body:
                    return None

                return json.loads(body)

        except urllib.error.HTTPError as error:
            try:
                response_body = error.read().decode("utf-8")
            except Exception:
                response_body = ""

            print("")
            print("GitHub API error")
            print(f"Status: {error.code}")
            print(f"URL: {url}")
            print(response_body)
            print("")

            return None

        except urllib.error.URLError as error:
            print("")
            print(f"Network error: {error}")
            print(f"URL: {url}")
            print("")

            if attempt < retries - 1:
                time.sleep(5)
                continue

            return None

    print("")
    print(
        f"No response available after "
        f"{retries} attempts:"
    )
    print(url)
    print("")

    return None


def paginated(url):
    """
    Yield all items from a paginated GitHub API endpoint.
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

        if data is None:
            break

        if not isinstance(data, list):
            print(
                f"Expected list response but received "
                f"{type(data).__name__}"
            )
            print(f"URL: {page_url}")
            break

        if len(data) == 0:
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
# DATA STORAGE
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
# LOAD ORGANIZATION REPOSITORIES
# ============================================================

print("")
print("========================================")
print(" Team Kaube Hall of Fame")
print("========================================")
print("")

print(f"Organization: {ORG}")
print(f"Cutoff date: {CUTOFF.isoformat()}")
print("")

print("Loading organization repositories...")
print("")

repositories = []

repo_url = (
    f"{API}/orgs/{ORG}/repos"
    f"?type=all"
    f"&sort=full_name"
    f"&direction=asc"
)

for repo in paginated(repo_url):

    # Ignore forks to prevent upstream code from
    # distorting the leaderboard.
    if repo.get("fork"):
        print(
            f"Skipping fork: "
            f"{repo.get('name', 'unknown')}"
        )
        continue

    # Ignore archived repositories.
    if repo.get("archived"):
        print(
            f"Skipping archived repo: "
            f"{repo.get('name', 'unknown')}"
        )
        continue

    repositories.append(repo)


print("")
print(
    f"Found {len(repositories)} accessible repositories."
)
print("")

print("Repositories visible to leaderboard token:")
print("")

for repo in repositories:
    visibility = repo.get("visibility")

    if not visibility:
        visibility = (
            "private"
            if repo.get("private")
            else "public"
        )

    print(
        f" - {repo['name']} "
        f"({visibility})"
    )

print("")


if len(repositories) == 0:
    raise RuntimeError(
        "The token cannot see any Team-Kaube repositories. "
        "Check HALL_OF_FAME_TOKEN permissions and "
        "organization approval."
    )


# ============================================================
# COMMIT + CODE STATISTICS
# ============================================================

print("")
print("========================================")
print(" Collecting commit statistics")
print("========================================")
print("")


for index, repo in enumerate(
    repositories,
    start=1,
):
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
        f"{repo_name} ({visibility})"
    )

    contributor_url = (
        f"{API}/repos/{ORG}/{repo_name}"
        f"/stats/contributors"
    )

    contributors = api_request(
        contributor_url,
        retries=10,
    )

    if contributors is None:
        print(
            f"  No contributor statistics "
            f"available for {repo_name}"
        )
        print("")
        continue

    if not isinstance(contributors, list):
        print(
            f"  Invalid contributor response "
            f"for {repo_name}"
        )
        print("")
        continue

    print(
        f"  Contributors returned: "
        f"{len(contributors)}"
    )

    repo_commits = 0
    repo_additions = 0
    repo_deletions = 0

    for contributor in contributors:
        author = contributor.get("author")

        # Commits that cannot be mapped to a GitHub
        # account have no author object.
        if not author:
            continue

        username = author.get("login")

        if not username:
            continue

        if is_bot(username):
            continue

        contributor_commits = 0
        contributor_additions = 0
        contributor_deletions = 0

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

            commits = week.get("c", 0)
            additions = week.get("a", 0)
            deletions = week.get("d", 0)

            contributor_commits += commits
            contributor_additions += additions
            contributor_deletions += deletions

        if (
            contributor_commits == 0
            and contributor_additions == 0
            and contributor_deletions == 0
        ):
            continue

        stats[username]["commits"] += (
            contributor_commits
        )

        stats[username]["additions"] += (
            contributor_additions
        )

        stats[username]["deletions"] += (
            contributor_deletions
        )

        repo_commits += contributor_commits
        repo_additions += contributor_additions
        repo_deletions += contributor_deletions

        print(
            f"    {username}: "
            f"{contributor_commits} commits, "
            f"+{contributor_additions}, "
            f"-{contributor_deletions}"
        )

    print(
        f"  Repository total: "
        f"{repo_commits} commits, "
        f"+{repo_additions}, "
        f"-{repo_deletions}"
    )
    print("")


# ============================================================
# PULL REQUESTS + REVIEWS
# ============================================================

print("")
print("========================================")
print(" Collecting pull request statistics")
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

    pull_url = (
        f"{API}/repos/{ORG}/{repo_name}/pulls"
        f"?state=all"
        f"&sort=updated"
        f"&direction=desc"
    )

    repo_prs = 0
    repo_reviews = 0

    for pull in paginated(pull_url):
        updated_at = parse_date(
            pull.get("updated_at")
        )

        # Since results are sorted newest first,
        # we can stop once PRs become too old.
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
            repo_prs += 1

        # ----------------------------------------------------
        # REVIEWS
        #
        # One user counts at most once per PR.
        # Multiple review submissions on the same PR do not
        # artificially increase the number.
        # ----------------------------------------------------

        reviewers_on_pr = set()

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

            reviewers_on_pr.add(reviewer)

        for reviewer in reviewers_on_pr:
            stats[reviewer]["reviews"] += 1
            repo_reviews += 1

    print(
        f"  Merged PRs: {repo_prs}"
    )

    print(
        f"  Reviewed PRs: {repo_reviews}"
    )

    print("")


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
# FAIL IF NOTHING WAS COLLECTED
# ============================================================

if not stats:
    print("")
    print("========================================")
    print(" ERROR")
    print("========================================")
    print("")
    print(
        "No contributor activity could be collected."
    )
    print("")
    print("Possible reasons:")
    print(
        " - HALL_OF_FAME_TOKEN is not approved "
        "for Team-Kaube"
    )
    print(
        " - The token does not have access to "
        "the organization repositories"
    )
    print(
        " - The token is missing required "
        "read permissions"
    )
    print(
        " - GitHub contributor statistics "
        "have not finished generating"
    )
    print(
        " - The repositories have no activity "
        "during the selected time period"
    )
    print("")

    raise RuntimeError(
        "No contributor statistics were collected."
    )


# ============================================================
# CALCULATE SCORE
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
        item[1]["reviews"],
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
# CONSOLE LEADERBOARD
# ============================================================

print("")
print("========================================")
print(" Collected leaderboard")
print("========================================")
print("")

for position, (username, values) in enumerate(
    ranking,
    start=1,
):
    print(
        f"{position}. "
        f"{username} | "
        f"{values['score']} pts | "
        f"{values['commits']} commits | "
        f"{values['merged_prs']} PRs | "
        f"{values['reviews']} reviews | "
        f"+{values['additions']} lines"
    )

print("")


# ============================================================
# BUILD README SECTION
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


# ============================================================
# PR MASTER
# ============================================================

lines.extend([
    "",
    "### 🔀 PR Master",
    "",
])

if pr_user and pr_value > 0:
    lines.append(
        "Most merged pull requests: "
        f"**{github_profile(pr_user)} "
        f"— {format_number(pr_value)} PRs**"
    )
else:
    lines.append(
        "_No merged pull requests during this period._"
    )


# ============================================================
# REVIEWER
# ============================================================

lines.extend([
    "",
    "### 👀 Reviewer",
    "",
])

if review_user and review_value > 0:
    lines.append(
        "Most reviewed pull requests: "
        f"**{github_profile(review_user)} "
        f"— {format_number(review_value)} reviews**"
    )
else:
    lines.append(
        "_No pull request reviews during this period._"
    )


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
    "Private repository names, PR titles and commit messages are never "
    "written to this README.",
    "</sub>",
    "",
    "<sub>",
    "🏆 Score: "
    "1 point per commit · "
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
print("========================================")
print(" Hall of Fame generated successfully")
print("========================================")
print("")
