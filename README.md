
# Copy the Whales — Mobile Website

This is a GitHub-ready Streamlit website for viewing the most common active Polymarket picks among top leaderboard traders.

## Deploy from your phone

### 1. Create a GitHub repository

In Safari, go to GitHub and create a new repository named:

`copy-the-whales`

A public repository is simplest for free deployment.

### 2. Upload these files

Upload everything from this folder while preserving the `.streamlit` folder:

- `app.py`
- `requirements.txt`
- `.streamlit/config.toml`

### 3. Deploy on Streamlit Community Cloud

1. Open Streamlit Community Cloud.
2. Sign in and connect GitHub.
3. Tap **Create app**.
4. Select your `copy-the-whales` repository.
5. Set the entrypoint to `app.py`.
6. Choose a website address.
7. Tap **Deploy**.

Your site will receive a `streamlit.app` web address that works on iPhone.

## Add it to your iPhone Home Screen

1. Open the deployed website in Safari.
2. Tap the Share button.
3. Tap **Add to Home Screen**.
4. Name it `Whales`.
5. Tap **Add**.

## How it works

The site reads the Polymarket leaderboard, retrieves each wallet’s current positions, excludes resolved/redeemable holdings by default, and ranks identical market/outcome combinations by:

1. Number of distinct whales holding the pick.
2. Combined current position value as the tie-breaker.

Each wallet counts only once per exact market and outcome.

## Refresh behavior

- Leaderboard cache: 5 minutes.
- Wallet positions cache: 3 minutes.
- Tap **Refresh live picks** to request a new snapshot.

## Disclaimer

This tool is for analytics and entertainment, not financial advice. Visible positions may be correlated, hedged elsewhere, delayed, or copied from another trader.
