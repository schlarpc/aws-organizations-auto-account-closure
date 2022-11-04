# aws-organizations-auto-account-closure

## About

AWS Organizations offers a `CloseAccount` API, but it has a low quota:
you may only close 10% of your organization's accounts within a rolling 30 day period.
This can be frustrating for small deployments, where a single account may put you over
quota for the month. If you want to close multiple accounts, you have to remember to revisit
the issue every month until all of them are closed.

This solution allows you to enqueue accounts for closure with tags,
automatically closing them whenever you have quota available.

## Usage

First, deploy [the CloudFormation template (`template.json`)](template.json)
to your organization management account.

When you want to close an account, tag it with the key `organizations:account-closure` and
the value `REQUESTED`.

On an hourly schedule, this automation will run, and attempt to close any tagged accounts.
Successfully closed accounts will then have their tag value changed to `ACKNOWLEDGED`.
