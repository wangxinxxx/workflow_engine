# Feishu Bridge

This directory contains the Feishu long-connection bridge migrated into the `workflow_engine` repository.

## Config

The bridge reads configuration from the repository root `.env`.

Required variables:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

Common runtime variables:

- `FEISHU_DEFAULT_CHAT_ID`
- `WORKSPACE_DIR`
- `WORKFLOW_WORKSPACE_DIR`
- `TEST_COMMAND`
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
- `/model`
- `/run-test`
- `/create-workflow --tapd-id <id> --short-name <slug> --brief <text>`
- `/codex <request>`
