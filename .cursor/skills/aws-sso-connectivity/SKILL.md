---
name: aws-sso-connectivity
description: AWS SSO login and connectivity workflow for this project. Use when user needs to connect to AWS, troubleshoot AWS authentication issues, or when AWS commands fail with credential errors. Handles SSO login, role assumption, and credential verification.
---

# AWS SSO Connectivity

Multi-step workflow for AWS SSO authentication with role assumption.

## Lightship AI MVP (`lightship-ai-mvp`) — **use this profile**

| Profile | Account | Region | Purpose |
|---------|---------|--------|---------|
| `lightship-proxy` | 304242047713 | us-east-1 | SSO base for Lightship chain |
| **`lightship`** | **336090301206** | **us-east-1** | **Assume `role-commit-lightship-devops` — all AWS work for this repo** |

1. `aws sso login --profile lightship-proxy`
2. `aws sts get-caller-identity --profile lightship --region us-east-1` → expect account **336090301206**.

Do **not** use `corp-ai-sandbox-devops` for this repository (different account).

## Other profiles (from `~/.aws/config`)

| Profile | Account | Region | Purpose |
|---------|---------|--------|---------|
| `proxy-corp-ai-devops` | 266731137418 | us-east-1 | SSO base/proxy profile |
| `corp-ai-sandbox-devops` | 095128162384 | us-east-1 | Other repos — `role-commit-corp-ai-devops` via proxy |
| `alidade-proxy` | 226760688258 | — | SSO base for Alidade account |
| `alidade-dev` | 533267103017 | us-east-1 | Assumes `role-commit-alidade-devops` via alidade-proxy |

> The SSO session is named `commit-sso`, backed by:
> - Start URL: `https://d-936707c4bd.awsapps.com/start`
> - SSO Region: `eu-west-1`

## Connection Workflow

### Step 1: Check Current Status (Lightship MVP)

```powershell
aws sts get-caller-identity --profile lightship --region us-east-1
```

**If successful**: Shows account **336090301206** → Already connected  
**If error**: Proceed to Step 2

### Step 2: SSO Login

```powershell
aws sso login --profile lightship-proxy
```

For non-Lightship work you may instead use `aws sso login --profile proxy-corp-ai-devops`.

This opens a browser for authentication. User must:
1. Click "Confirm and continue"
2. Click "Allow"
3. Return to terminal

### Step 3: Verify Connection (Lightship MVP)

```powershell
aws sts get-caller-identity --profile lightship --region us-east-1
```

Expected output includes **Account** `336090301206` and **Arn** containing `role-commit-lightship-devops`.

## Cloning AWS CodeCommit Repos

CodeCommit repos require AWS credentials via `git-remote-codecommit` (GRC) or credential helper.

### Prerequisites

Install `git-remote-codecommit` via the `py` launcher (Python 3.10 on this machine):

```powershell
py -m pip install git-remote-codecommit
```

> **Important**: After installing, add the Python Scripts folder to PATH in your current session
> so git can find the `git-remote-codecommit` helper:
>
> ```powershell
> $env:PATH += ";C:\Users\sagil\AppData\Local\Programs\Python\Python310\Scripts"
> ```

### Clone Command (us-east-2 CodeCommit)

```powershell
git clone codecommit::us-east-2://corp-ai-sandbox-devops@<repo-name> <local-folder>
```

**Example** — clone `alidade-ai-agentcore-poc`:

```powershell
git clone codecommit::us-east-2://corp-ai-sandbox-devops@alidade-ai-agentcore-poc C:\Git_Repos\Alidade_MVP\alidade-ai-agentcore-poc
```

> The `codecommit::` protocol uses `git-remote-codecommit` and automatically
> signs requests with the specified AWS profile. No HTTPS credentials needed.

## Troubleshooting

### Error: "Token has expired"

```powershell
aws sso login --profile proxy-corp-ai-devops
```

### Error: "InvalidClientTokenId"

SSO session expired or invalid:
```powershell
aws sso logout
aws sso login --profile proxy-corp-ai-devops
```

### Error: "could not find profile"

Verify profile exists:
```powershell
Select-String -Path "$env:USERPROFILE\.aws\config" -Pattern "lightship" -Context 0,5
```

### Error: "region inconsistency between profile and sso-session"

Always pass `--region` explicitly instead of relying on the profile default:
```powershell
aws sts get-caller-identity --profile lightship --region us-east-1
```

### Error: "AccessDenied" on specific service

The role may not have permissions for that service. Check with admin.

## Quick Commands Reference

| Task | Command |
|------|---------|
| Login to SSO (Lightship) | `aws sso login --profile lightship-proxy` |
| Check identity (Lightship) | `aws sts get-caller-identity --profile lightship --region us-east-1` |
| Clone this CodeCommit repo | `git clone codecommit::us-east-1://lightship@lightship-ai-mvp` |
| Logout | `aws sso logout` |

## Session Duration

- **SSO session**: ~8-12 hours
- **Assumed role**: 1 hour (auto-refreshed while SSO is valid)

## For Python/boto3 Code

```python
import boto3

session = boto3.Session(profile_name='lightship', region_name='us-east-1')
client = session.client('s3')  # example
```
