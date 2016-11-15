#!/usr/bin/python3
# -*- coding: utf-8 -*-

import argparse
import json


def get_params():
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--filepath", required=True,
                        help="JSON file with the projects in old format")
    args = parser.parse_args()
    return args

if __name__ == '__main__':
    args = get_params()

    ds_supported = ['git', 'github']

    projects_new = {}

    with open(args.filepath) as f:
        projects_old = json.load(f)
        for project in projects_old:
            projects_new[project] = {}
            for ds in projects_old[project]:
                if ds in ds_supported:
                    projects_new[project][ds] = []
                    for repo in projects_old[project][ds]:
                        projects_new[project][ds].append(repo['url'])

    print(json.dumps(projects_new, indent=4, sort_keys=True))
