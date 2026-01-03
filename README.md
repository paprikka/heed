# HN RSS feeds

<img src="bg.webp" width=300>

RSS feeds of popular HN bloggers, based on the data from [this post](https://refactoringenglish.com/blog/2025-hn-top-5/) by Michael Lynch

HN discussion: https://news.ycombinator.com/item?id=46478377

## Download

- [OPML - import to your RSS Reader](./LIST.opml) (psst here's a free and privacy-friendly one: [NetNewsWire](https://netnewswire.com/))
- [Markdown](./LIST.md)
- [CSV](./LIST.csv)
- [JSON](./LIST.json)

## Setup

```bash
uv run find_feeds.py                  # process all domains
uv run find_feeds.py --limit 10       # process first 10 domains
uv run find_feeds.py --concurrency 20 # limit concurrent requests (default: 50)
uv run find_feeds.py --timeout 60     # timeout per domain in seconds (default: 30)
```

Input: `domains.json`
Output: `LIST.json`, `LIST.md`, `LIST.csv`, `LIST.opml`

## Next

[Context](https://untested.sonnet.io/notes/share-your-unfinished-scrappy-work/): This is a quick 30 min mostly vibe-coded project for [rafal](https://sonnet.io) so he could upload it to his favourite [RSS Reader](https://netnewswire.com/). Be kind, be curious.

- [ ] update domains.json automatically on a schedule
- [ ] add a write-up on untested
- [ ] replace "feed online" with a status / comments column
- [ ] support sites with multiple feeds

![flower](flower.png)
