name: 🏆 Update Hall of Fame

on:
  schedule:
    - cron: "0 4 * * *"

  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: hall-of-fame
  cancel-in-progress: true

jobs:
  update-hall-of-fame:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Generate Hall of Fame
        env:
          ORG: Team-Kaube
          GH_TOKEN: ${{ secrets.HALL_OF_FAME_TOKEN }}
        run: python scripts/hall_of_fame.py

      - name: Commit changes
        run: |
          if git diff --quiet profile/README.md; then
            echo "Hall of Fame already up to date."
            exit 0
          fi

          git config user.name "Team Kaube Bot"
          git config user.email "github-actions[bot]@users.noreply.github.com"

          git add profile/README.md
          git commit -m "chore: update Hall of Fame"
          git push
