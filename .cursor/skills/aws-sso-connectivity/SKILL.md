---
name: aws-sso-connectivity
description: AWS SSO login and connectivity workflow for this project. Use when user needs to connect to AWS, troubleshoot AWS authentication issues, or when AWS commands fail with credential errors. Handles SSO login, role assumption, and credential verification.
---

# AWS SSO Connectivity

Multi-step workflow for AWS SSO authentication with role assumption.

## Configured Profiles (from `~/.aws/config`)

| Profile | Account | Region | Purpose |
|---------|---------|--------|---------|
| `proxy-corp-ai-devops` | 266731137418 | us-east-1 | SSO base/proxy profile |
| `corp-ai-sandbox-devops` | 095128162384 | us-east-1 | **Default** - Assumes `role-commit-corp-ai-devops` via proxy |
| `alidade-proxy` | 226760688258 | — | SSO base for Alidade account |
| `alidade-dev` | 533267103017 | us-east-1 | Assumes `role-commit-alidade-devops` via alidade-proxy |

> The SSO session is named `commit-sso`, backed by:
> - Start URL: `https://d-936707c4bd.awsapps.com/start`
> - SSO Region: `eu-west-1`

## Connection Workflow

### Step 1: Check Current Status

```powershell
aws sts get-caller-identity --profile corp-ai-sandbox-devops --region us-east-2
```

**If successful**: Shows account/role info → Already connected  
**If error**: Proceed to Step 2

### Step 2: SSO Login

```powershell
aws sso login --profile proxy-corp-ai-devops
```

This opens a browser for authentication. User must:
1. Click "Confirm and continue"
2. Click "Allow"
3. Return to terminal

### Step 3: Verify Connection

```powershell
aws sts get-caller-identity --profile corp-ai-sandbox-devops --region us-east-2
```

Expected output:
```json
{
    "UserId": "AROARMJQUSBIH2HVZRTZA:sagil-cli",
    "Account": "095128162384",
    "Arn": "arn:aws:sts::095128162384:assumed-role/role-commit-corp-ai-devops/sagil-cli"
}
```

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
Select-String -Path "$env:USERPROFILE\.aws\config" -Pattern "corp-ai-sandbox-devops" -Context 0,5
```

### Error: "region inconsistency between profile and sso-session"

Always pass `--region` explicitly instead of relying on the profile default:
```powershell
aws sts get-caller-identity --profile corp-ai-sandbox-devops --region us-east-2
```

### Error: "AccessDenied" on specific service

The role may not have permissions for that service. Check with admin.

## Quick Commands Reference

| Task | Command |
|------|---------|
| Login to SSO | `aws sso login --profile proxy-corp-ai-devops` |
| Check identity | `aws sts get-caller-identity --profile corp-ai-sandbox-devops --region us-east-2` |
| Clone CodeCommit repo | `git clone codecommit::us-east-2://corp-ai-sandbox-devops@<repo-name>` |
| List S3 buckets | `aws s3 ls --profile corp-ai-sandbox-devops` |
| Logout | `aws sso logout` |

## Session Duration

- **SSO session**: ~8-12 hours
- **Assumed role**: 1 hour (auto-refreshed while SSO is valid)

## For Python/boto3 Code

```python
import boto3

session = boto3.Session(profile_name='corp-ai-sandbox-devops', region_name='us-east-2')
client = session.client('bedrock-runtime')
```
