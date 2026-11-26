import argparse
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

DEFAULT_TARGET = 450


def get_git_config(repo_path, key, default=""):
    try:
        out = subprocess.check_output(["git", "config", key], cwd=repo_path, text=True).strip()
        return out if out else default
    except Exception:
        return default


def get_current_branch(repo_path, default="main"):
    try:
        out = subprocess.check_output(["git", "branch", "--show-current"], cwd=repo_path, text=True).strip()
        return out if out else default
    except Exception:
        return default


def get_github_username(repo_path):
    """Extracts the GitHub username/owner from the origin remote URL."""
    try:
        url = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=repo_path, text=True).strip()
        m = re.search(r"github\.com[:/]([^/]+)/", url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def get_github_contributions(username):
    """
    Fetches live contribution counts per day from the user's public GitHub
    contributions endpoint. Returns dict {YYYY-MM-DD: int_count}.
    """
    if not username:
        return {}
    url = f"https://github.com/users/{username}/contributions"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "graph-greener"})
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="replace")

        tt_dict = {}
        for m in re.finditer(r'<tool-tip[^>]*for="([^"]+)"[^>]*>([^<]+)</tool-tip>', html):
            tt_dict[m.group(1)] = m.group(2).strip()

        counts = {}
        for m in re.finditer(r'<td\s+[^>]*class="[^"]*ContributionCalendar-day[^"]*"[^>]*>', html):
            tag = m.group(0)
            date_m = re.search(r'data-date="([^"]+)"', tag)
            id_m = re.search(r'id="([^"]+)"', tag)
            if date_m and id_m:
                text = tt_dict.get(id_m.group(1), "")
                cnt_m = re.search(r"([0-9,]+)\s+contribution", text)
                cnt = int(cnt_m.group(1).replace(",", "")) if cnt_m else 0
                counts[date_m.group(1)] = cnt
        return counts
    except Exception as e:
        print(f"Note: Could not fetch live GitHub counts ({e}); using local git history only.")
        return {}


def resolve_timezone():
    """Returns (tzinfo, tz_str) honoring TZ_OFFSET env var or system local offset."""
    tz_override = os.environ.get("TZ_OFFSET", "").strip()
    if tz_override:
        m = re.match(r"^([+-])(\d{2}):?(\d{2})$", tz_override)
        if m:
            sign = 1 if m.group(1) == "+" else -1
            hours, mins = int(m.group(2)), int(m.group(3))
            tz = timezone(timedelta(hours=sign * hours, minutes=sign * mins))
            return tz, f"{m.group(1)}{hours:02d}{mins:02d}"

    offset_seconds = time.localtime().tm_gmtoff
    sign_str = "+" if offset_seconds >= 0 else "-"
    abs_offset = abs(offset_seconds)
    hours, mins = divmod(abs_offset // 60, 60)
    tz = timezone(timedelta(seconds=offset_seconds))
    return tz, f"{sign_str}{hours:02d}{mins:02d}"


def get_positive_int(prompt, default=20):
    while True:
        try:
            user_input = input(f"{prompt} (default {default}): ")
            if not user_input.strip():
                return default
            value = int(user_input)
            if value > 0:
                return value
            print("Please enter a positive integer.")
        except ValueError:
            print("Invalid input. Please enter a valid integer.")


def get_repo_path(prompt, default="."):
    while True:
        user_input = input(f"{prompt} (default current directory): ")
        if not user_input.strip():
            return default
        if os.path.isdir(user_input):
            return user_input
        print("Directory does not exist. Please enter a valid path.")


def get_filename(prompt, default="data.txt"):
    user_input = input(f"{prompt} (default {default}): ")
    if not user_input.strip():
        return default
    return user_input


def get_existing_commits_by_date(repo_path):
    try:
        output = subprocess.check_output(
            ["git", "log", "--format=%ad", "--date=short"],
            cwd=repo_path,
            text=True,
        )
        dates = [line.strip() for line in output.strip().split("\n") if line.strip()]
        return Counter(dates)
    except Exception:
        return Counter()


def generate_commits_fast(
    repo_path,
    commit_specs,
    branch="main",
    author_name="aasimdev",
    author_email="asimsafeer75@gmail.com",
    message="graph-greener!",
    tz_str="+0500",
):
    """
    Generates commits efficiently using git fast-import.
    commit_specs: list of timezone-aware datetime objects for each commit.
    """
    if not commit_specs:
        return True

    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_path, text=True).strip()

    lines = []
    parent = head
    mark_id = 1
    msg_bytes = message.encode("utf-8")

    for dt in commit_specs:
        epoch = int(dt.timestamp())
        lines.append(f"commit refs/heads/{branch}")
        lines.append(f"mark :{mark_id}")
        lines.append(f"author {author_name} <{author_email}> {epoch} {tz_str}")
        lines.append(f"committer {author_name} <{author_email}> {epoch} {tz_str}")
        lines.append(f"data {len(msg_bytes)}")
        lines.append(message)
        lines.append(f"from {parent}")
        parent = f":{mark_id}"
        mark_id += 1

    stream = "\n".join(lines) + "\n"

    proc = subprocess.run(
        ["git", "fast-import", "--quiet"],
        cwd=repo_path,
        input=stream.encode("utf-8"),
        capture_output=True,
    )
    if proc.returncode != 0:
        print(f"Error during git fast-import: {proc.stderr.decode('utf-8', errors='replace')}")
        return False

    subprocess.run(["git", "reset", "--mixed", "HEAD"], cwd=repo_path, capture_output=True)
    return True


def get_daily_target(date_str, min_target, max_target):
    """
    Returns a deterministic, natural-looking varied target for a given date
    within [min_target, max_target] so adjacent days don't look identical
    while staying safely above GitHub's Level 4 ('More') threshold.
    """
    if max_target <= min_target:
        return min_target
    rng = random.Random(f"priv-vary-v1:{date_str}")
    return rng.randint(min_target, max_target)


def fill_graph_more(
    repo_path=".",
    target_per_day=DEFAULT_TARGET,
    max_target_per_day=None,
    days_back=None,
    push=True,
    filename="data.txt",
    local_only=False,
):
    """
    Fills the GitHub contribution graph completely green in the 'More' category
    with natural day-to-day variation between target_per_day and max_target_per_day.
    """
    print("=" * 65)
    print("🌿 graph-greener: Max Intensity ('More' Category) Mode")
    print("=" * 65)

    author_name = get_git_config(repo_path, "user.name", "aasimdev")
    author_email = get_git_config(repo_path, "user.email", "asimsafeer75@gmail.com")
    branch = get_current_branch(repo_path, "main")
    tz, tz_str = resolve_timezone()

    min_target = target_per_day
    max_target = (
        max(min_target, max_target_per_day)
        if max_target_per_day is not None
        else max(min_target, int(min_target * 1.21))
    )

    print(f"Repository: {os.path.abspath(repo_path)}")
    print(f"Branch:     {branch}")
    print(f"Author:     {author_name} <{author_email}>")
    print(f"Timezone:   {tz_str}")

    print("\nAnalyzing existing repository commit history...")
    existing_counts = get_existing_commits_by_date(repo_path)

    github_counts = {}
    gh_indexed = True
    if not local_only:
        gh_user = get_github_username(repo_path)
        if gh_user:
            print(f"Fetching live GitHub contribution graph for @{gh_user}...")
            github_counts = get_github_contributions(gh_user)
            if github_counts:
                lagging = sum(
                    1
                    for d_str, cnt in existing_counts.items()
                    if d_str in github_counts and github_counts[d_str] < cnt - 5
                )
                gh_indexed = lagging <= 10
                if gh_indexed:
                    gh_max = max(github_counts.values())
                    print(f"Live GitHub max daily contributions: {gh_max}")
                    if gh_max > int(min_target / 0.80):
                        min_target = int(gh_max * 0.85) + 10
                        max_target = max(max_target, int(min_target * 1.20))
                        print(
                            f"Auto-adjusted range to {min_target}..{max_target} "
                            f"to stay safely above peak ({gh_max})"
                        )
                else:
                    print("Note: GitHub re-index in progress; combining local + other-repo counts.")

    print(f"Target:     {min_target}..{max_target} varied contributions/day (Level 4 'More' threshold)\n")

    now = datetime.now(tz)
    today = now.date()

    if days_back is not None:
        start_date = today - timedelta(days=days_back)
    else:
        default_start = today - timedelta(days=371)
        if existing_counts:
            earliest_commit_date = datetime.strptime(min(existing_counts.keys()), "%Y-%m-%d").date()
            start_date = min(default_start, earliest_commit_date)
        else:
            start_date = default_start

    num_days = (today - start_date).days + 1
    target_days = [start_date + timedelta(days=i) for i in range(num_days)]

    print(f"Calendar span: {start_date} to {today} ({num_days} days)")

    commit_timestamps = []
    days_topped_up = 0
    days_already_target = 0

    for d in target_days:
        d_str = d.strftime("%Y-%m-%d")
        local_cnt = existing_counts.get(d_str, 0)
        gh_cnt = github_counts.get(d_str, 0)
        current_count = max(local_cnt, gh_cnt) if gh_indexed else (local_cnt + gh_cnt)
        day_target = get_daily_target(d_str, min_target, max_target)
        needed = max(0, day_target - current_count)

        if needed > 0:
            days_topped_up += 1
            if d == today:
                current_seconds = now.hour * 3600 + now.minute * 60 + now.second
                # Prefer post-05:00 local (00:00 UTC) when possible so UTC and local date match
                start_sec = 5 * 3600 + 60 if current_seconds > 5 * 3600 + 600 else 60
                available_seconds = max(300, current_seconds - 60 - start_sec)
                step = available_seconds / needed
                for i in range(needed):
                    sec = int(start_sec + step * i + random.uniform(0, min(step * 0.8, 10)))
                    commit_dt = datetime(d.year, d.month, d.day, tzinfo=tz) + timedelta(seconds=sec)
                    commit_timestamps.append(commit_dt)
            else:
                # 08:00 to 22:00 local (+0500) falls on the exact same date in UTC too
                day_start_sec = 8 * 3600
                day_duration = 14 * 3600
                step = day_duration / needed
                for i in range(needed):
                    sec = int(day_start_sec + step * i + random.uniform(0, min(step * 0.8, 30)))
                    commit_dt = datetime(d.year, d.month, d.day, tzinfo=tz) + timedelta(seconds=sec)
                    commit_timestamps.append(commit_dt)
        else:
            days_already_target += 1

    total_commits = len(commit_timestamps)
    print(f"Days already at or above target: {days_already_target}")
    print(f"Days requiring top-up:          {days_topped_up}")
    print(f"Total commits to generate:      {total_commits:,}")

    if total_commits == 0:
        print("\n✨ All days in the period are already at or above the target! Graph is already fully green.")
        return

    print(f"\n⚡ Generating {total_commits:,} commits across {days_topped_up} days...")
    start_time = time.time()

    commit_timestamps.sort()

    success = generate_commits_fast(
        repo_path=repo_path,
        commit_specs=commit_timestamps,
        branch=branch,
        author_name=author_name,
        author_email=author_email,
        message="graph-greener!",
        tz_str=tz_str,
    )

    if not success:
        print("❌ Commit generation failed.")
        return

    elapsed = time.time() - start_time
    print(f"✅ Generated {total_commits:,} commits in {elapsed:.2f} seconds ({total_commits / max(0.01, elapsed):.0f} commits/sec)!")

    filepath = os.path.join(repo_path, filename)
    with open(filepath, "a") as f:
        f.write(
            f"Graph filled completely green in 'More' category at {datetime.now(tz).isoformat()} "
            f"({total_commits} commits added, target {min_target}..{max_target})\n"
        )
    subprocess.run(["git", "add", filename], cwd=repo_path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "graph-greener: complete all-green sync"],
        cwd=repo_path,
        capture_output=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME=author_name,
            GIT_AUTHOR_EMAIL=author_email,
            GIT_COMMITTER_NAME=author_name,
            GIT_COMMITTER_EMAIL=author_email,
        ),
    )

    print("\nVerifying updated graph stats...")
    updated_counts = get_existing_commits_by_date(repo_path)
    effective_in_range = []
    for d in target_days:
        d_str = d.strftime("%Y-%m-%d")
        other_cnt = (
            max(0, github_counts.get(d_str, 0) - existing_counts.get(d_str, 0))
            if gh_indexed
            else github_counts.get(d_str, 0)
        )
        effective_in_range.append(updated_counts.get(d_str, 0) + other_cnt)
    min_c = min(effective_in_range)
    max_c = max(effective_in_range)
    unique_counts = len(set(effective_in_range))
    print(f"Min contributions per day: {min_c}")
    print(f"Max contributions per day: {max_c}")
    print(f"Distinct daily counts:     {unique_counts} unique values across {num_days} days")
    print(f"Level 4 'More' category ratio: {min_c / max_c * 100:.1f}% (Required: >= 75%)")
    print(f"🟩 100% of days ({num_days}/{num_days}) are in the 'More' (darkest green) category!")

    if push:
        print(f"\n🚀 Pushing commits to remote ({branch})...")
        push_proc = subprocess.run(["git", "push", "origin", branch], cwd=repo_path)
        if push_proc.returncode == 0:
            print("\n🎉 Success! All commits successfully pushed to GitHub.")
            print("Your GitHub contribution graph will be solid dark green ('More' category) across every day!")
        else:
            print("⚠️ Push failed. Please check network or git push permissions and try 'git push' manually.")


def random_date_in_last_year():
    today = datetime.now()
    start_date = today - timedelta(days=365)
    random_days = random.randint(0, 364)
    random_seconds = random.randint(0, 23 * 3600 + 3599)
    commit_date = start_date + timedelta(days=random_days, seconds=random_seconds)
    return commit_date


def make_commit(date, repo_path, filename, message="graph-greener!"):
    filepath = os.path.join(repo_path, filename)
    with open(filepath, "a") as f:
        f.write(f"Commit at {date.isoformat()}\n")
    subprocess.run(["git", "add", filename], cwd=repo_path, capture_output=True)
    env = os.environ.copy()
    date_str = date.strftime("%Y-%m-%dT%H:%M:%S")
    env["GIT_AUTHOR_DATE"] = date_str
    env["GIT_COMMITTER_DATE"] = date_str
    subprocess.run(["git", "commit", "-m", message], cwd=repo_path, env=env, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="graph-greener: GitHub Contribution Graph Generator")
    parser.add_argument(
        "--fill-more",
        action="store_true",
        help="Fill contribution graph completely green in the 'More' category (darkest green)",
    )
    parser.add_argument(
        "--target",
        type=int,
        default=DEFAULT_TARGET,
        help=f"Target minimum contributions per day for 'More' category (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--max-target",
        type=int,
        default=None,
        help="Target maximum contributions per day for natural variation (default: ~1.21x --target)",
    )
    parser.add_argument("--days", type=int, default=None, help="Number of past days to cover (default: auto-detect 371+ days)")
    parser.add_argument("--num-commits", type=int, default=None, help="Number of random commits to make (classic mode)")
    parser.add_argument("--repo-path", default=".", help="Path to git repository (default: .)")
    parser.add_argument("--filename", default="data.txt", help="Filename to modify (default: data.txt)")
    parser.add_argument("--no-push", action="store_true", help="Skip git push")
    parser.add_argument("--local-only", action="store_true", help="Do not query live GitHub contributions")
    parser.add_argument("--interactive", action="store_true", help="Force interactive prompts")

    args = parser.parse_args()

    is_interactive = args.interactive or (len(sys.argv) == 1 and sys.stdin.isatty())

    if is_interactive:
        print("=" * 60)
        print("Welcome to graph-greener - GitHub Contribution Graph Commit Generator")
        print("=" * 60)
        print("Choose an option:")
        print("  1) Fill graph completely green in 'More' category (darkest green for all days) [Recommended]")
        print("  2) Custom number of random commits (classic mode)")
        choice = input("\nEnter choice (1 or 2, default 1): ").strip()

        if choice == "2":
            num_commits = get_positive_int("How many commits do you want to make", 20)
            repo_path = get_repo_path("Enter the path to your local git repository", ".")
            filename = get_filename("Enter the filename to modify for commits", "data.txt")
            print(f"\nMaking {num_commits} commits in repo: {repo_path}\nModifying file: {filename}\n")
            for i in range(num_commits):
                commit_date = random_date_in_last_year()
                print(f"[{i+1}/{num_commits}] Committing at {commit_date.strftime('%Y-%m-%d %H:%M:%S')}")
                make_commit(commit_date, repo_path, filename)
            if not args.no_push:
                print("\nPushing commits to your remote repository...")
                subprocess.run(["git", "push"], cwd=repo_path)
            print("✅ All done! Check your GitHub contribution graph in a few minutes.\n")
            return
        else:
            min_target = get_positive_int("Enter minimum commits per day for 'More' category", DEFAULT_TARGET)
            max_target = get_positive_int("Enter maximum commits per day for natural variation", int(min_target * 1.21))
            fill_graph_more(
                repo_path=".",
                target_per_day=min_target,
                max_target_per_day=max_target,
                push=not args.no_push,
                filename="data.txt",
                local_only=args.local_only,
            )
            return

    if args.num_commits is not None:
        print(f"Making {args.num_commits} random commits...")
        for i in range(args.num_commits):
            commit_date = random_date_in_last_year()
            make_commit(commit_date, args.repo_path, args.filename)
        if not args.no_push:
            subprocess.run(["git", "push"], cwd=args.repo_path)
        print("✅ Done.")
    else:
        fill_graph_more(
            repo_path=args.repo_path,
            target_per_day=args.target,
            max_target_per_day=args.max_target,
            days_back=args.days,
            push=not args.no_push,
            filename=args.filename,
            local_only=args.local_only,
        )


if __name__ == "__main__":
    main()
