#!/usr/bin/env python3
"""Seed the Catalog table. Repeatable: re-running overwrites the same five products.

Usage: python scripts/seed.py --stack campus-cafe [--region us-east-1] [--profile NAME]
       python scripts/seed.py --table <CatalogTableName>
"""
import argparse
import json
import os

import boto3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", help="CloudFormation stack name (reads the CatalogTableName output)")
    parser.add_argument("--table", help="Catalog table name (skips the stack lookup)")
    parser.add_argument("--region")
    parser.add_argument("--profile")
    args = parser.parse_args()
    if not (args.stack or args.table):
        parser.error("pass --stack or --table")

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    table_name = args.table
    if not table_name:
        stack = session.client("cloudformation").describe_stacks(StackName=args.stack)["Stacks"][0]
        table_name = next(o["OutputValue"] for o in stack["Outputs"] if o["OutputKey"] == "CatalogTableName")

    with open(os.path.join(os.path.dirname(__file__), "seed_data.json")) as f:
        products = json.load(f)
    table = session.resource("dynamodb").Table(table_name)
    for product in products:
        table.put_item(Item=product)
    print(f"Seeded {len(products)} products into {table_name} ({session.region_name})")


if __name__ == "__main__":
    main()
