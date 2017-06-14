#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Github to Elastic class helper
#
# Copyright (C) 2015 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors:
#   Alvaro del Castillo San Felix <acs@bitergia.com>
#

import json
import logging
import os
import pickle
import re

from datetime import datetime

from sortinghat.exceptions import NotFoundError

from .utils import get_time_diff_days, unixtime_to_datetime
from .enrich import Enrich, metadata


GITHUB = 'https://github.com/'
logger = logging.getLogger(__name__)


class GitHubEnrich(Enrich):

    roles = ['assignee_data', 'user_data']

    def __init__(self, db_sortinghat=None, db_projects_map=None, json_projects_map=None,
                 db_user='', db_password='', db_host=''):
        super().__init__(db_sortinghat, db_projects_map, json_projects_map,
                         db_user, db_password, db_host)
        self.users = {}  # cache users
        self.location = {}  # cache users location
        self.location_not_found = []  # location not found in map api

    def set_elastic(self, elastic):
        self.elastic = elastic
        # Recover cache data from Elastic
        self.geolocations = self.geo_locations_from_es()

    def get_field_author(self):
        return "user_data"

    def get_field_date(self):
        """ Field with the date in the JSON enriched items """
        return "grimoire_creation_date"

    def get_fields_uuid(self):
        return ["assignee_uuid", "user_uuid"]

    def get_identities(self, item):
        """ Return the identities from an item """
        identities = []

        item = item['data']

        for identity in ['user', 'assignee']:
            if item[identity]:
                # In user_data we have the full user data
                user = self.get_sh_identity(item[identity+"_data"])
                if user:
                    identities.append(user)
        return identities

    def get_sh_identity(self, item, identity_field=None):
        identity = {}

        user = item  # by default a specific user dict is expected
        if 'data' in item and type(item) == dict:
            user = item['data'][identity_field]

        if not user:
            return identity

        identity['username'] = user['login']
        identity['email'] = None
        identity['name'] = None
        if 'email' in user:
            identity['email'] = user['email']
        if 'name' in user:
            identity['name'] = user['name']
        return identity

    def get_geo_point(self, location):
        geo_point = geo_code = None

        if location is None:
            return geo_point

        if location in self.geolocations:
            geo_location = self.geolocations[location]
            geo_point = {
                    "lat": geo_location['lat'],
                    "lon": geo_location['lon']
            }

        elif location in self.location_not_found:
            # Don't call the API.
            pass

        else:
            url = 'https://maps.googleapis.com/maps/api/geocode/json'
            params = {'sensor': 'false', 'address': location}
            r = self.requests.get(url, params=params)

            try:
                logger.debug("Using Maps API to find %s" % (location))
                r_json = r.json()
                geo_code = r_json['results'][0]['geometry']['location']
            except:
                if location not in self.location_not_found:
                    logger.debug("Can't find geocode for " + location)
                    self.location_not_found.append(location)

            if geo_code:
                geo_point = {
                    "lat": geo_code['lat'],
                    "lon": geo_code['lng']
                }
                self.geolocations[location] = geo_point


        return geo_point


    def get_github_cache(self, kind, _key):
        """ Get cache data for items of _type using _key as the cache dict key """

        cache = {}
        res_size = 100  # best size?
        _from = 0

        index_github = "github/" + kind

        url = self.elastic.url + "/"+index_github
        url += "/_search" + "?" + "size=%i" % res_size
        r = self.requests.get(url)
        type_items = r.json()

        if 'hits' not in type_items:
            logger.info("No github %s data in ES" % (kind))

        else:
            while len(type_items['hits']['hits']) > 0:
                for hit in type_items['hits']['hits']:
                    item = hit['_source']
                    cache[item[_key]] = item
                _from += res_size
                r = self.requests.get(url+"&from=%i" % _from)
                type_items = r.json()
                if 'hits' not in type_items:
                    break

        return cache


    def geo_locations_from_es(self):
        return self.get_github_cache("geolocations", "location")

    def geo_locations_to_es(self):
        max_items = self.elastic.max_items_bulk
        current = 0
        bulk_json = ""

        url = self.elastic.url + "/github/geolocations/_bulk"

        logger.debug("Adding geoloc to %s (in %i packs)" % (url, max_items))


        for loc in self.geolocations:
            if current >= max_items:
                self.requests.put(url, data=bulk_json)
                bulk_json = ""
                current = 0

            geopoint = self.geolocations[loc]
            location = geopoint.copy()
            location["location"] = loc
            # First upload the raw issue data to ES
            data_json = json.dumps(location)
            # Don't include in URL non ascii codes
            safe_loc = str(loc.encode('ascii', 'ignore'),'ascii')
            geo_id = str("%s-%s-%s" % (location["lat"], location["lon"],
                                       safe_loc))
            bulk_json += '{"index" : {"_id" : "%s" } }\n' % (geo_id)
            bulk_json += data_json +"\n"  # Bulk document
            current += 1

        self.requests.put(url, data = bulk_json)

        logger.debug("Adding geoloc to ES Done")


    def get_elastic_mappings(self):
        """ geopoints type is not created in dynamic mapping """

        mapping = """
        {
            "properties": {
               "assignee_geolocation": {
                   "type": "geo_point"
               },
               "user_geolocation": {
                   "type": "geo_point"
               },
               "title_analyzed": {
                 "type": "string",
                 "index":"analyzed"
               }
            }
        }
        """

        return {"items":mapping}

    def get_project_repository(self, eitem):
        repo = eitem['origin']
        return repo

    @metadata
    def get_rich_item(self, item):
        rich_issue = {}

        for f in self.RAW_FIELDS_COPY:
            if f in item:
                rich_issue[f] = item[f]
            else:
                rich_issue[f] = None
        # The real data
        issue = item['data']

        rich_issue['time_to_close_days'] = \
            get_time_diff_days(issue['created_at'], issue['closed_at'])

        if issue['state'] != 'closed':
            rich_issue['time_open_days'] = \
                get_time_diff_days(issue['created_at'], datetime.utcnow())
        else:
            rich_issue['time_open_days'] = rich_issue['time_to_close_days']

        rich_issue['user_login'] = issue['user']['login']
        user = issue['user_data']

        if user is not None:
            rich_issue['user_name'] = user['name']
            rich_issue['author_name'] = user['name']
            rich_issue['user_email'] = user['email']
            if rich_issue['user_email']:
                rich_issue["user_domain"] = self.get_email_domain(rich_issue['user_email'])
            rich_issue['user_org'] = user['company']
            rich_issue['user_location'] = user['location']
            rich_issue['user_geolocation'] = self.get_geo_point(user['location'])
        else:
            rich_issue['user_name'] = None
            rich_issue['user_email'] = None
            rich_issue["user_domain"] = None
            rich_issue['user_org'] = None
            rich_issue['user_location'] = None
            rich_issue['user_geolocation'] = None
            rich_issue['author_name'] = None


        assignee = None

        if issue['assignee'] is not None:
            assignee = issue['assignee_data']
            rich_issue['assignee_login'] = issue['assignee']['login']
            rich_issue['assignee_name'] = assignee['name']
            rich_issue['assignee_email'] = assignee['email']
            if rich_issue['assignee_email']:
                rich_issue["assignee_domain"] = self.get_email_domain(rich_issue['assignee_email'])
            rich_issue['assignee_org'] = assignee['company']
            rich_issue['assignee_location'] = assignee['location']
            rich_issue['assignee_geolocation'] = \
                self.get_geo_point(assignee['location'])
        else:
            rich_issue['assignee_name'] = None
            rich_issue['assignee_login'] = None
            rich_issue['assignee_email'] = None
            rich_issue["assignee_domain"] = None
            rich_issue['assignee_org'] = None
            rich_issue['assignee_location'] = None
            rich_issue['assignee_geolocation'] = None

        rich_issue['id'] = issue['id']
        rich_issue['id_in_repo'] = issue['html_url'].split("/")[-1]
        rich_issue['repository'] = issue['html_url'].rsplit("/", 2)[0]
        rich_issue['title'] = issue['title']
        rich_issue['title_analyzed'] = issue['title']
        rich_issue['state'] = issue['state']
        rich_issue['created_at'] = issue['created_at']
        rich_issue['updated_at'] = issue['updated_at']
        rich_issue['closed_at'] = issue['closed_at']
        rich_issue['url'] = issue['html_url']
        labels = ''
        if 'labels' in issue:
            for label in issue['labels']:
                labels += label['name']+";;"
        if labels != '':
            labels[:-2]
        rich_issue['labels'] = labels

        rich_issue['pull_request'] = True
        rich_issue['item_type'] = 'pull request'
        if not 'head' in issue.keys() and not 'pull_request' in issue.keys():
            rich_issue['pull_request'] = False
            rich_issue['item_type'] = 'issue'

        rich_issue['github_repo'] = rich_issue['repository'].replace(GITHUB,'')
        rich_issue['github_repo'] = re.sub('.git$', '', rich_issue['github_repo'])
        rich_issue["url_id"] = rich_issue['github_repo']+"/issues/"+rich_issue['id_in_repo']

        if self.prjs_map:
            rich_issue.update(self.get_item_project(rich_issue))

        if 'project' in item:
            rich_issue['project'] = item['project']

        rich_issue.update(self.get_grimoire_fields(issue['created_at'], "issue"))

        if self.sortinghat:
            item[self.get_field_date()] = rich_issue[self.get_field_date()]
            rich_issue.update(self.get_item_sh(item, self.roles))

        return rich_issue

    def __read_users_data(self, users_file):
        users_data = {}
        # The list of users is from git and some could be externals git to github
        users_git_no_github = []
        users_data_found = 0
        users_not_found = []
        users_multigithub = []

        logger.debug("Loading username data from  %s", users_file)
        import csv
        with open(users_file, newline='') as f:
            users = csv.reader(f, delimiter=',', quotechar='"')
            next(users)  # Pass the headers line
            for row in users:
                # "Author","author_uuid: Descending","project","Commits",
                # "Projects","Max author_date","Min author_date"
                name = row[0]
                uuid = row[1]
                project = row[2]
                project_activity = row[3]
                max_author_date = row[5]
                min_author_date = row[6]
                # We need also the username
                usernames = []
                try:
                    for identity in self.get_unique_identity(uuid).identities:
                        if identity.source == 'github':
                            if usernames:
                                if identity.username not in usernames:
                                    logger.warning('%s %s github with several usernames %s %s', name, uuid, identity.username, usernames)
                                    if uuid not in users_multigithub:
                                        users_multigithub += [uuid]
                                    usernames += [identity.username]
                            else:
                                usernames = [identity.username]
                    users_data_found += 1
                except NotFoundError:
                    if uuid not in users_not_found:
                        users_not_found += [uuid]
                    logger.error('%s %s uuid not found in SH db', name, uuid)

                if not usernames:
                    if uuid not in users_git_no_github:
                        users_git_no_github += [uuid]
                    # logger.debug('%s %s github usernames not found', name, uuid)

                if uuid not in users_data:
                    users_data[uuid] = {
                        "name": name,
                        "usernames": usernames,
                        "mozilla_project": project,
                        "mozilla_commits": project_activity,
                        "mozilla_projects": [project],
                        "mozilla_commits_max_author_date": max_author_date,
                        "mozilla_commits_min_author_date": min_author_date
                    }
                else:
                    users_data[uuid]['mozilla_projects'] += [project]
                    users_data[uuid]['usernames'] = usernames
                    if users_data[uuid]['mozilla_commits'] > project_activity:
                        users_data[uuid]['mozilla_commits'] = project_activity
                        users_data[uuid]['mozilla_project'] = project
                    if users_data[uuid]['mozilla_commits_max_author_date'] < max_author_date:
                        users_data[uuid]['mozilla_commits_max_author_date'] = max_author_date
                    if users_data[uuid]['mozilla_commits_min_author_date'] > min_author_date:
                        users_data[uuid]['mozilla_commits_min_author_date'] = min_author_date

        total_users = len(users_data.keys())
        logger.debug("Total users in file: %i", total_users)
        logger.debug("Total users data (one line per repository) found ok: %i", users_data_found)
        logger.debug("Total users not found : %i", len(users_not_found))
        logger.debug("Total users from git not in github: %i", len(users_git_no_github))
        logger.debug("Total users from github : %i", total_users-len(users_git_no_github)-len(users_not_found))
        logger.debug("Total users with several github usernames: %i", len(users_multigithub))

        return users_data

    def enrich_users_activity(self, ocean_backend):
        # The final item enriched format is
        # issue: id, date, involves+name username en github,
        # main Mozilla project with git activity, list of Mozilla projects,
        # github organization from url, repository

        def build_involves(username, users_data):
            involves = {
                "involves_uuid": None,
                "involves_name": None,
                "involves_username": username
            }
            for uuid in users_data:
                if username in users_data[uuid]['usernames']:
                    involves['involves_uuid'] = uuid
                    involves['involves_name'] = users_data[uuid]['name']
                    break
            return involves

        # Firs step is to load the identities info we already has
        EXTRA_USER_NAME_INFO = 'git_top_authors.csv'

        users_data = {}
        # Try to recover from a previous pickle file created
        user_pickle_file = "users_data.pickle"
        # In production mode don't try to load a pickle file with old data
        if os.path.isfile(user_pickle_file) and False:
            with open(user_pickle_file, "rb") as fpick:
                users_data = pickle.load(fpick)
        else:
            users_data = self.__read_users_data(EXTRA_USER_NAME_INFO)
            with open(user_pickle_file, "wb") as fpick:
                pickle.dump(users_data, fpick, protocol=pickle.HIGHEST_PROTOCOL)

        for item in ocean_backend.fetch():
            issue = item['data']
            grimoire_fields = self.get_grimoire_fields(issue['created_at'], "issue")
            item[self.get_field_date()] = grimoire_fields[self.get_field_date()]
            # In raw data for activty we don't have assignee_data and user_data
            # roles = ['assignee', 'user']
            # sh_fields = self.get_item_sh(item, roles)

            # In origin we have the username, and the uuids in users_data
            # https://github.com/carols10cents/None/None
            involves_username = item['origin'].split('/')[3]
            involves_data = build_involves(involves_username, users_data)

            try:
                eitem = {
                    'id': item['uuid'],
                    'origin': item['origin'],
                    'tag': item['tag'],
                    'project': users_data[involves_data['involves_uuid']]['mozilla_project'],
                    'project_commits': int(users_data[involves_data['involves_uuid']]['mozilla_commits']),
                    'projects': users_data[involves_data['involves_uuid']]['mozilla_projects'],
                    'github_organization': issue['html_url'].rsplit("/", 4)[1],
                    'github_repository': issue['html_url'].rsplit("/", 4)[2]
                }
            except Exception as ex:
                # There are github usernames in the raw index from old collections
                # that are not in our curret users_data
                continue

            # date fields
            max_date = int(users_data[involves_data['involves_uuid']]['mozilla_commits_max_author_date'])
            eitem['max_date_author_commits'] = unixtime_to_datetime(max_date/1000).isoformat()
            min_date = int(users_data[involves_data['involves_uuid']]['mozilla_commits_min_author_date'])
            eitem['min_date_author_commits'] = unixtime_to_datetime(min_date/1000).isoformat()

            # eitem.update(sh_fields)
            eitem.update(grimoire_fields)
            eitem.update(involves_data)

            eitem['pull_request'] = 1
            eitem['issue'] = 0
            if not 'head' in issue.keys() and not 'pull_request' in issue.keys():
                eitem['pull_request'] = 0
                eitem['issue'] = 1

            yield eitem

    def enrich_items(self, ocean_backend):

        total = 0

        items = ocean_backend.fetch()
        # Check the origin of the first one to define the kind of enrichment
        try:
            item = next(items)
        except StopIteration:
            return total
        if item['origin'].endswith('/None/None'):
            logging.info("Enriching user activity in GitHub for issues")
            eitems = self.enrich_users_activity(ocean_backend)
            total = self.elastic.bulk_upload_sync(eitems, 'id')
        else:
            total = super(GitHubEnrich, self).enrich_items(ocean_backend)

            logger.debug("Updating GitHub users geolocations in Elastic")
            self.geo_locations_to_es() # Update geolocations in Elastic

        return total


class GitHubUser(object):
    """ Helper class to manage data from a Github user """

    users = {}  # cache with users from github

    def __init__(self, user):

        self.login = user['login']
        self.email = user['email']
        if 'company' in user:
            self.company = user['company']
        self.orgs = user['orgs']
        self.org = self._getOrg()
        self.name = user['name']
        self.location = user['location']


    def _getOrg(self):
        company = None

        if self.company:
            company = self.company

        if company is None:
            company = ''
            # Return the list of orgs
            for org in self.orgs:
                company += org['login'] +";;"
            company = company[:-2]

        return company
