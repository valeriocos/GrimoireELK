# -*- coding: utf-8 -*-
#
# Copyright (C) 2015-2019 Bitergia
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

import logging

from .enrich import Enrich, metadata
from ..elastic_mapping import Mapping as BaseMapping

from grimoirelab_toolkit.datetime import (str_to_datetime,
                                          datetime_utcnow,
                                          unixtime_to_datetime)


MAX_SIZE_BULK_ENRICHED_ITEMS = 200
REVIEW_TYPE = 'review'
COMMENT_TYPE = 'comment'

logger = logging.getLogger(__name__)


class Mapping(BaseMapping):

    @staticmethod
    def get_elastic_mappings(es_major):
        """Get Elasticsearch mapping.

        :param es_major: major version of Elasticsearch, as string
        :returns:        dictionary with a key, 'items', with the mapping
        """

        mapping = """
        {
            "properties": {
               "status": {
                  "type": "keyword"
               },
               "summary_analyzed": {
                  "type": "text"
               },
               "timeopen": {
                  "type": "double"
               }
            }
        }
        """

        return {"items": mapping}


class GerritEnrich(Enrich):

    mapping = Mapping

    def __init__(self, db_sortinghat=None, db_projects_map=None, json_projects_map=None,
                 db_user='', db_password='', db_host=''):
        super().__init__(db_sortinghat, db_projects_map, json_projects_map,
                         db_user, db_password, db_host)

        self.studies = []
        self.studies.append(self.enrich_demography)
        self.studies.append(self.enrich_onion)

    def get_field_author(self):
        return "owner"

    def get_fields_uuid(self):
        return ["review_uuid", "patchSet_uuid", "approval_uuid"]

    def get_sh_identity(self, item, identity_field=None):
        identity = {}

        user = item  # by default a specific user dict is expected
        if 'data' in item and type(item) == dict:
            user = item['data'][identity_field]
        elif identity_field:
            user = item[identity_field]

        identity['name'] = None
        identity['username'] = None
        identity['email'] = None

        if 'name' in user:
            identity['name'] = user['name']
        if 'email' in user:
            identity['email'] = user['email']
        if 'username' in user:
            identity['username'] = user['username']
        return identity

    def get_project_repository(self, eitem):
        repo = eitem['origin']
        repo += "_" + eitem['repository']
        return repo

    def get_identities(self, item):
        """Return the identities from an item"""

        item = item['data']

        # Changeset owner
        user = item['owner']
        identity = self.get_sh_identity(user)
        yield identity

        # Patchset uploader and author
        if 'patchSets' in item:
            for patchset in item['patchSets']:
                user = patchset['uploader']
                identity = self.get_sh_identity(user)
                yield identity
                if 'author' in patchset:
                    user = patchset['author']
                    identity = self.get_sh_identity(user)
                    yield identity
                if 'approvals' in patchset:
                    # Approvals by
                    for approval in patchset['approvals']:
                        user = approval['by']
                        identity = self.get_sh_identity(user)
                        yield identity

        # Comments reviewers
        if 'comments' in item:
            for comment in item['comments']:
                user = comment['reviewer']
                identity = self.get_sh_identity(user)
                yield identity

    def get_item_id(self, eitem):
        """ Return the item_id linked to this enriched eitem """

        # The eitem _id includes also the patch.
        return eitem["_source"]["review_id"]

    def _fix_review_dates(self, item):
        """Convert dates so ES detect them"""

        for date_field in ['timestamp', 'createdOn', 'lastUpdated']:
            if date_field in item.keys():
                date_ts = item[date_field]
                item[date_field] = unixtime_to_datetime(date_ts).isoformat()

        if 'patchSets' in item.keys():
            for patch in item['patchSets']:
                pdate_ts = patch['createdOn']
                patch['createdOn'] = unixtime_to_datetime(pdate_ts).isoformat()

                if 'approvals' in patch:
                    for approval in patch['approvals']:
                        adate_ts = approval['grantedOn']
                        approval['grantedOn'] = unixtime_to_datetime(adate_ts).isoformat()

        if 'comments' in item.keys():
            for comment in item['comments']:
                cdate_ts = comment['timestamp']
                comment['timestamp'] = unixtime_to_datetime(cdate_ts).isoformat()

    @metadata
    def get_rich_item(self, item):
        eitem = {}  # Item enriched

        for f in self.RAW_FIELDS_COPY:
            if f in item:
                eitem[f] = item[f]
            else:
                eitem[f] = None
        eitem['closed'] = item['metadata__updated_on']
        # The real data
        review = item['data']
        self._fix_review_dates(review)

        # data fields to copy
        copy_fields = ["status", "branch", "url"]
        for f in copy_fields:
            eitem[f] = review[f]
        # Fields which names are translated
        map_fields = {"subject": "summary",
                      "id": "githash",
                      "createdOn": "opened",
                      "project": "repository",
                      "number": "number"
                      }
        for fn in map_fields:
            eitem[map_fields[fn]] = review[fn]

        # Add id info to allow to coexistence of items of different types in the same index
        eitem['id'] = eitem['number']
        eitem["summary_analyzed"] = eitem["summary"]
        eitem["summary"] = eitem["summary"][:self.KEYWORD_MAX_SIZE]
        eitem["name"] = None
        eitem["domain"] = None
        if 'name' in review['owner']:
            eitem["name"] = review['owner']['name']
            if 'email' in review['owner']:
                if '@' in review['owner']['email']:
                    eitem["domain"] = review['owner']['email'].split("@")[1]
        # New fields generated for enrichment
        eitem["patchsets"] = len(review["patchSets"])

        # Time to add the time diffs
        created_on = review['createdOn']
        if len(review["patchSets"]) > 0:
            created_on = review["patchSets"][0]['createdOn']

        created_on_date = str_to_datetime(created_on)
        eitem["created_on"] = created_on

        eitem["last_updated"] = review['lastUpdated']
        last_updated_date = str_to_datetime(review['lastUpdated'])

        seconds_day = float(60 * 60 * 24)
        if eitem['status'] in ['MERGED', 'ABANDONED']:
            timeopen = \
                (last_updated_date - created_on_date).total_seconds() / seconds_day
        else:
            timeopen = \
                (datetime_utcnow() - created_on_date).total_seconds() / seconds_day
        eitem["timeopen"] = '%.2f' % timeopen

        if self.sortinghat:
            eitem.update(self.get_item_sh(item))

        if self.prjs_map:
            eitem.update(self.get_item_project(eitem))

        eitem.update(self.get_grimoire_fields(review['createdOn'], "review"))

        self.add_metadata_filter_raw(eitem)
        return eitem

    def get_rich_item_comments(self, comments, eitem):
        ecomments = []

        for comment in comments:
            ecomment = {}

            for f in self.RAW_FIELDS_COPY:
                ecomment[f] = eitem[f]

            # Copy data from the enriched review
            ecomment['url'] = eitem['url']
            ecomment['summary'] = eitem['summary']
            ecomment['repository'] = eitem['repository']
            ecomment['branch'] = eitem['branch']
            ecomment['review_number'] = eitem['number']

            # Add reviewer info
            ecomment["reviewer_name"] = None
            ecomment["reviewer_domain"] = None
            if 'reviewer' in comment and 'name' in comment['reviewer']:
                ecomment["reviewer_name"] = comment['reviewer']['name']
                if 'email' in comment['reviewer']:
                    if '@' in comment['reviewer']['email']:
                        ecomment["reviewer_domain"] = comment['reviewer']['email'].split("@")[1]

            # Add comment-specific data
            created = str_to_datetime(comment['timestamp'])
            ecomment['created'] = created.isoformat()
            ecomment['message'] = comment['message'][:self.KEYWORD_MAX_SIZE]

            # Add id info to allow to coexistence of items of different types in the same index
            ecomment['id'] = '{}_comment_{}'.format(ecomment['review_number'], created.timestamp())
            ecomment['type'] = COMMENT_TYPE

            if self.sortinghat:
                ecomment.update(self.get_item_sh(comment, ['reviewer'], 'timestamp'))

                ecomment['author_id'] = ecomment['reviewer_id']
                ecomment['author_uuid'] = ecomment['reviewer_uuid']
                ecomment['author_name'] = ecomment['reviewer_name']
                ecomment['author_user_name'] = ecomment['reviewer_user_name']
                ecomment['author_domain'] = ecomment['reviewer_domain']
                ecomment['author_gender'] = ecomment['reviewer_gender']
                ecomment['author_gender_acc'] = ecomment['reviewer_gender_acc']
                ecomment['author_org_name'] = ecomment['reviewer_org_name']
                ecomment['author_bot'] = ecomment['reviewer_bot']

            if self.prjs_map:
                ecomment.update(self.get_item_project(ecomment))

            ecomment.update(self.get_grimoire_fields(comment['timestamp'], COMMENT_TYPE))

            self.add_metadata_filter_raw(ecomment)
            ecomments.append(ecomment)

        return ecomments

    def get_field_unique_id(self):
        return "id"

    def enrich_items(self, ocean_backend):
        items_to_enrich = []
        num_items = 0
        ins_items = 0

        for item in ocean_backend.fetch():
            eitem = self.get_rich_item(item)

            items_to_enrich.append(eitem)

            comments = item['data'].get('comments', [])
            if comments:
                rich_item_comments = self.get_rich_item_comments(comments, eitem)
                items_to_enrich.extend(rich_item_comments)

            if len(items_to_enrich) < MAX_SIZE_BULK_ENRICHED_ITEMS:
                continue

            num_items += len(items_to_enrich)
            ins_items += self.elastic.bulk_upload(items_to_enrich, self.get_field_unique_id())
            items_to_enrich = []

        if len(items_to_enrich) > 0:
            num_items += len(items_to_enrich)
            ins_items += self.elastic.bulk_upload(items_to_enrich, self.get_field_unique_id())

        if num_items != ins_items:
            missing = num_items - ins_items
            logger.error("%s/%s missing items for Gerrit", str(missing), str(num_items))
        else:
            logger.info("%s items inserted for Gerrit", str(num_items))

        return num_items

    def enrich_demography(self, ocean_backend, enrich_backend, date_field="grimoire_creation_date",
                          author_field="author_uuid"):

        super().enrich_demography(ocean_backend, enrich_backend, date_field, author_field=author_field)

    def enrich_onion(self, ocean_backend, enrich_backend,
                     no_incremental=False,
                     in_index='gerrit_onion-src',
                     out_index='gerrit_onion-enriched',
                     data_source='gerrit',
                     contribs_field='uuid',
                     timeframe_field='grimoire_creation_date',
                     sort_on_field='metadata__timestamp',
                     seconds=Enrich.ONION_INTERVAL):

        super().enrich_onion(enrich_backend=enrich_backend,
                             in_index=in_index,
                             out_index=out_index,
                             data_source=data_source,
                             contribs_field=contribs_field,
                             timeframe_field=timeframe_field,
                             sort_on_field=sort_on_field,
                             no_incremental=no_incremental,
                             seconds=seconds)
