# Feishu Bridge

This directory contains the Feishu long-connection message bridge in the `workflow_engine` repository.

The bridge is intentionally narrow: it receives Feishu text messages, emits a receipt, and responds only to a small set of bridge health commands. It does not spawn local workflows, tests, or other subprocesses.

## Config

The bridge reads configuration from the repository root `.env`.

Required variables:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

Common runtime variables:

- `FEISHU_DEFAULT_CHAT_ID`
- `PORT`
- `HOST`

## Install

From this directory:

```bash
npm install
```

## Start

From this directory:

```bash
npm start
```

## Health Check

```bash
curl http://127.0.0.1:8787/health
```

## Commands

- `/ping`
- `/status`
- `/help`
