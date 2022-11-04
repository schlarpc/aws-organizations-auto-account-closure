#!/usr/bin/env python3

from awacs import logs, organizations, states, sts, tag
from awacs.aws import (
    Allow,
    Condition,
    ForAllValuesStringEquals,
    PolicyDocument,
    Principal,
    Statement,
    StringEquals,
)
from troposphere import (
    AccountId,
    Join,
    Partition,
    Select,
    Split,
    StackId,
    StackName,
    Template,
)
from troposphere.events import Rule, Target
from troposphere.iam import Policy, Role
from troposphere.logs import LogGroup
from troposphere.stepfunctions import (
    CloudWatchLogsLogGroup,
    LogDestination,
    LoggingConfiguration,
    StateMachine,
)

ACCOUNT_CLOSURE_TAG = "organizations:account-closure"


def create_template():
    template = Template(
        Description="Queue accounts for deletion with organizations:CloseAccount"
    )

    log_group = template.add_resource(
        LogGroup(
            "LogGroup",
            LogGroupName=Join(
                "/",
                ["/aws/vendedlogs/states", StackName, Select(2, Split("/", StackId))],
            ),
            RetentionInDays=365,
        )
    )

    state_machine_role = template.add_resource(
        Role(
            "StateMachineRole",
            AssumeRolePolicyDocument=PolicyDocument(
                Version="2012-10-17",
                Statement=[
                    Statement(
                        Effect=Allow,
                        Principal=Principal("Service", "states.amazonaws.com"),
                        Action=[sts.AssumeRole],
                    ),
                ],
            ),
            Policies=[
                Policy(
                    PolicyName="OrganizationsAccountClosure",
                    PolicyDocument=PolicyDocument(
                        Version="2012-10-17",
                        Statement=[
                            Statement(
                                Effect=Allow,
                                Action=[organizations.TagResource],
                                Resource=[
                                    Join(
                                        ":",
                                        [
                                            "arn",
                                            Partition,
                                            "organizations",
                                            "",
                                            AccountId,
                                            Join("/", ["account", "*"]),
                                        ],
                                    ),
                                ],
                                Condition=Condition(
                                    [
                                        ForAllValuesStringEquals(
                                            {
                                                "aws:TagKeys": [ACCOUNT_CLOSURE_TAG],
                                            }
                                        ),
                                        StringEquals(
                                            {
                                                f"aws:ResourceTag/{ACCOUNT_CLOSURE_TAG}": [
                                                    "REQUESTED"
                                                ],
                                                f"aws:RequestTag/{ACCOUNT_CLOSURE_TAG}": [
                                                    "ACKNOWLEDGED"
                                                ],
                                            }
                                        ),
                                    ]
                                ),
                            ),
                            Statement(
                                Effect=Allow,
                                Action=[
                                    organizations.CloseAccount,
                                    organizations.DescribeAccount,
                                ],
                                Resource=[
                                    Join(
                                        ":",
                                        [
                                            "arn",
                                            Partition,
                                            "organizations",
                                            "",
                                            AccountId,
                                            Join("/", ["account", "*"]),
                                        ],
                                    ),
                                ],
                                Condition=Condition(
                                    [
                                        StringEquals(
                                            {
                                                f"aws:ResourceTag/{ACCOUNT_CLOSURE_TAG}": [
                                                    "REQUESTED"
                                                ],
                                            }
                                        )
                                    ]
                                ),
                            ),
                            Statement(
                                Effect=Allow,
                                Action=[tag.GetResources],
                                Resource=["*"],
                            ),
                            Statement(
                                Effect=Allow,
                                Action=[
                                    logs.CreateLogDelivery,
                                    logs.DeleteLogDelivery,
                                    logs.DescribeLogGroups,
                                    logs.DescribeResourcePolicies,
                                    logs.GetLogDelivery,
                                    logs.ListLogDeliveries,
                                    logs.PutLogEvents,
                                    logs.PutResourcePolicy,
                                    logs.UpdateLogDelivery,
                                ],
                                Resource=["*"],
                            ),
                        ],
                    ),
                ),
            ],
        )
    )

    state_machine = template.add_resource(
        StateMachine(
            "OrganizationsAccountClosureStateMachine",
            Definition={
                "Comment": "Close Organizations accounts tagged for closure",
                "StartAt": "Set empty pagination token",
                "States": {
                    "Set empty pagination token": {
                        "Type": "Pass",
                        "Next": "Find accounts tagged for closure",
                        "Result": {"PaginationToken": ""},
                    },
                    "Find accounts tagged for closure": {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::aws-sdk:resourcegroupstaggingapi:getResources",
                        "Parameters": {
                            "ResourceTypeFilters": ["organizations:account"],
                            "TagFilters": [
                                {
                                    "Key": ACCOUNT_CLOSURE_TAG,
                                    "Values": ["REQUESTED"],
                                }
                            ],
                            "PaginationToken.$": "$.PaginationToken",
                        },
                        "Next": "For each account tagged REQUESTED",
                    },
                    "For each account tagged REQUESTED": {
                        "Type": "Map",
                        "ItemsPath": "$.ResourceTagMappingList",
                        "MaxConcurrency": 1,
                        "Iterator": {
                            "StartAt": "Describe account",
                            "States": {
                                "Describe account": {
                                    "Type": "Task",
                                    "Resource": "arn:aws:states:::aws-sdk:organizations:describeAccount",
                                    "Parameters": {
                                        "AccountId.$": "States.ArrayGetItem(States.StringSplit($.ResourceARN, '/'), 2)"
                                    },
                                    "Next": "Is account ACTIVE?",
                                    "Catch": [
                                        {
                                            "Comment": "Potential eventual consistency issue",
                                            "ErrorEquals": [
                                                "Organizations.AccountNotFoundException"
                                            ],
                                            "Next": "Nothing to do",
                                        }
                                    ],
                                },
                                "Is account ACTIVE?": {
                                    "Type": "Choice",
                                    "Choices": [
                                        {
                                            "Variable": "$.Account.Status",
                                            "StringEquals": "ACTIVE",
                                            "Next": "Close account",
                                        }
                                    ],
                                    "Default": "Tag with ACKNOWLEDGED",
                                },
                                "Close account": {
                                    "Type": "Task",
                                    "Resource": "arn:aws:states:::aws-sdk:organizations:closeAccount",
                                    "Parameters": {"AccountId.$": "$.Account.Id"},
                                    "Next": "Tag with ACKNOWLEDGED",
                                    "Catch": [
                                        {
                                            "Comment": "Account closure limit reached",
                                            "ErrorEquals": [
                                                "Organizations.ConstraintViolationException",
                                            ],
                                            "Next": "Nothing to do",
                                        }
                                    ],
                                    "Retry": [
                                        {
                                            "Comment": "Retry on throttle",
                                            "ErrorEquals": [
                                                "Organizations.TooManyRequestsException",
                                            ],
                                            "BackoffRate": 1,
                                            "IntervalSeconds": 20,
                                            "MaxAttempts": 2,
                                        }
                                    ],
                                },
                                "Tag with ACKNOWLEDGED": {
                                    "Type": "Task",
                                    "Resource": "arn:aws:states:::aws-sdk:organizations:tagResource",
                                    "Parameters": {
                                        "ResourceId.$": "$.Account.Id",
                                        "Tags": [
                                            {
                                                "Key": ACCOUNT_CLOSURE_TAG,
                                                "Value": "ACKNOWLEDGED",
                                            }
                                        ],
                                    },
                                    "End": True,
                                },
                                "Nothing to do": {"Type": "Succeed"},
                            },
                        },
                        # would discard this with "null" but CFN is bad
                        "ResultPath": "$._MapResult",
                        "Next": "Additional pages?",
                    },
                    "Additional pages?": {
                        "Type": "Choice",
                        "Choices": [
                            {
                                "Variable": "$.PaginationToken",
                                "StringEquals": "",
                                "Next": "Done paginating",
                            }
                        ],
                        "Default": "Find accounts tagged for closure",
                    },
                    "Done paginating": {"Type": "Succeed"},
                },
                "TimeoutSeconds": 300,
            },
            LoggingConfiguration=LoggingConfiguration(
                Level="ALL",
                IncludeExecutionData=True,
                Destinations=[
                    LogDestination(
                        CloudWatchLogsLogGroup=CloudWatchLogsLogGroup(
                            LogGroupArn=log_group.get_att("Arn"),
                        ),
                    ),
                ],
            ),
            RoleArn=state_machine_role.get_att("Arn"),
            StateMachineType="EXPRESS",
        )
    )

    rule_role = template.add_resource(
        Role(
            "RuleRole",
            AssumeRolePolicyDocument=PolicyDocument(
                Version="2012-10-17",
                Statement=[
                    Statement(
                        Effect=Allow,
                        Principal=Principal("Service", "events.amazonaws.com"),
                        Action=[sts.AssumeRole],
                    ),
                ],
            ),
            Policies=[
                Policy(
                    PolicyName="StateMachineInvoke",
                    PolicyDocument=PolicyDocument(
                        Version="2012-10-17",
                        Statement=[
                            Statement(
                                Effect=Allow,
                                Action=[states.StartExecution],
                                Resource=[
                                    state_machine.get_att("Arn"),
                                ],
                            ),
                        ],
                    ),
                ),
            ],
        )
    )

    template.add_resource(
        Rule(
            "Rule",
            ScheduleExpression="rate(1 hour)",
            Targets=[
                Target(
                    Id="default",
                    Arn=state_machine.ref(),
                    RoleArn=rule_role.get_att("Arn"),
                ),
            ],
        )
    )

    return template


if __name__ == "__main__":
    print(create_template().to_json())
