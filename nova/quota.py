# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Quotas for resources per project."""

import copy
import datetime

from oslo_log import log as logging
from oslo_utils import importutils
from oslo_utils import timeutils
import six

import nova.conf
from nova import context as nova_context
from nova import db
from nova import exception
from nova.i18n import _LE
from nova import objects
from nova import utils

LOG = logging.getLogger(__name__)


CONF = nova.conf.CONF


class DbQuotaDriver(object):
    """Driver to perform necessary checks to enforce quotas and obtain
    quota information.  The default driver utilizes the local
    database.
    """
    UNLIMITED_VALUE = -1

    def get_by_project_and_user(self, context, project_id, user_id, resource):
        """Get a specific quota by project and user."""

        return objects.Quotas.get(context, project_id, resource,
                                  user_id=user_id)

    def get_by_project(self, context, project_id, resource):
        """Get a specific quota by project."""

        return objects.Quotas.get(context, project_id, resource)

    def get_by_class(self, context, quota_class, resource):
        """Get a specific quota by quota class."""

        return objects.Quotas.get_class(context, quota_class, resource)

    def get_defaults(self, context, resources):
        """Given a list of resources, retrieve the default quotas.
        Use the class quotas named `_DEFAULT_QUOTA_NAME` as default quotas,
        if it exists.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        """

        quotas = {}
        default_quotas = objects.Quotas.get_default_class(context)
        for resource in resources.values():
            # resource.default returns the config options. So if there's not
            # an entry for the resource in the default class, it uses the
            # config option.
            quotas[resource.name] = default_quotas.get(resource.name,
                                                       resource.default)

        return quotas

    def get_class_quotas(self, context, resources, quota_class,
                         defaults=True):
        """Given a list of resources, retrieve the quotas for the given
        quota class.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param quota_class: The name of the quota class to return
                            quotas for.
        :param defaults: If True, the default value will be reported
                         if there is no specific value for the
                         resource.
        """

        quotas = {}
        class_quotas = objects.Quotas.get_all_class_by_name(context,
                                                            quota_class)
        for resource in resources.values():
            if defaults or resource.name in class_quotas:
                quotas[resource.name] = class_quotas.get(resource.name,
                                                         resource.default)

        return quotas

    def _process_quotas(self, context, resources, project_id, quotas,
                        quota_class=None, defaults=True, usages=None,
                        remains=False):
        modified_quotas = {}
        # Get the quotas for the appropriate class.  If the project ID
        # matches the one in the context, we use the quota_class from
        # the context, otherwise, we use the provided quota_class (if
        # any)
        if project_id == context.project_id:
            quota_class = context.quota_class
        if quota_class:
            class_quotas = objects.Quotas.get_all_class_by_name(context,
                                                                quota_class)
        else:
            class_quotas = {}

        default_quotas = self.get_defaults(context, resources)

        for resource in resources.values():
            # Omit default/quota class values
            if not defaults and resource.name not in quotas:
                continue

            limit = quotas.get(resource.name, class_quotas.get(
                        resource.name, default_quotas[resource.name]))
            modified_quotas[resource.name] = dict(limit=limit)

            # Include usages if desired.  This is optional because one
            # internal consumer of this interface wants to access the
            # usages directly from inside a transaction.
            if usages:
                usage = usages.get(resource.name, {})
                modified_quotas[resource.name].update(
                    in_use=usage.get('in_use', 0),
                    reserved=0,
                    )

            # Initialize remains quotas with the default limits.
            if remains:
                modified_quotas[resource.name].update(remains=limit)

        if remains:
            # Get all user quotas for a project and subtract their limits
            # from the class limits to get the remains. For example, if the
            # class/default is 20 and there are two users each with quota of 5,
            # then there is quota of 10 left to give out.
            all_quotas = objects.Quotas.get_all(context, project_id)
            for quota in all_quotas:
                if quota.resource in modified_quotas:
                    modified_quotas[quota.resource]['remains'] -= \
                            quota.hard_limit

        return modified_quotas

    def _get_usages(self, context, resources, project_id, user_id=None):
        """Get usages of specified resources.

        This function is called to get resource usages for validating quota
        limit creates or updates in the os-quota-sets API and for displaying
        resource usages in the os-used-limits API. This function is not used
        for checking resource usage against quota limits.

        :param context: The request context for access checks
        :param resources: The dict of Resources for which to get usages
        :param project_id: The project_id for scoping the usage count
        :param user_id: Optional user_id for scoping the usage count
        :returns: A dict containing resources and their usage information,
                  for example:
                  {'project_id': 'project-uuid',
                   'user_id': 'user-uuid',
                   'instances': {'in_use': 5},
                   'fixed_ips': {'in_use': 5}}
        """
        usages = {}
        for resource in resources.values():
            # NOTE(melwitt): This is to keep things working while we're in the
            # middle of converting ReservableResources to CountableResources.
            # We should skip resources that are not countable and eventually
            # when there are no more ReservableResources, we won't need this.
            if not isinstance(resource, CountableResource):
                continue
            if resource.name in usages:
                # This is needed because for any of the resources:
                # ('instances', 'cores', 'ram'), they are counted at the same
                # time for efficiency (query the instances table once instead
                # of multiple times). So, a count of any one of them contains
                # counts for the others and we can avoid re-counting things.
                continue
            if resource.name in ('key_pairs', 'server_group_members',
                                 'security_group_rules'):
                # These per user resources are special cases whose usages
                # are not considered when validating limit create/update or
                # displaying used limits. They are always zero.
                usages[resource.name] = {'in_use': 0}
            else:
                if resource.name in db.quota_get_per_project_resources():
                    count = resource.count_as_dict(context, project_id)
                    key = 'project'
                else:
                    # NOTE(melwitt): This assumes a specific signature for
                    # count_as_dict(). Usages used to be records in the
                    # database but now we are counting resources. The
                    # count_as_dict() function signature needs to match this
                    # call, else it should get a conditional in this function.
                    count = resource.count_as_dict(context, project_id,
                                                   user_id=user_id)
                    key = 'user' if user_id else 'project'
                # Example count_as_dict() return value:
                #   {'project': {'instances': 5},
                #    'user': {'instances': 2}}
                counted_resources = count[key].keys()
                for res in counted_resources:
                    count_value = count[key][res]
                    usages[res] = {'in_use': count_value}
        return usages

    def get_user_quotas(self, context, resources, project_id, user_id,
                        quota_class=None, defaults=True,
                        usages=True, project_quotas=None,
                        user_quotas=None):
        """Given a list of resources, retrieve the quotas for the given
        user and project.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param project_id: The ID of the project to return quotas for.
        :param user_id: The ID of the user to return quotas for.
        :param quota_class: If project_id != context.project_id, the
                            quota class cannot be determined.  This
                            parameter allows it to be specified.  It
                            will be ignored if project_id ==
                            context.project_id.
        :param defaults: If True, the quota class value (or the
                         default value, if there is no value from the
                         quota class) will be reported if there is no
                         specific value for the resource.
        :param usages: If True, the current counts will also be returned.
        :param project_quotas: Quotas dictionary for the specified project.
        :param user_quotas: Quotas dictionary for the specified project
                            and user.
        """
        if user_quotas:
            user_quotas = user_quotas.copy()
        else:
            user_quotas = objects.Quotas.get_all_by_project_and_user(
                context, project_id, user_id)
        # Use the project quota for default user quota.
        proj_quotas = project_quotas or objects.Quotas.get_all_by_project(
            context, project_id)
        for key, value in proj_quotas.items():
            if key not in user_quotas.keys():
                user_quotas[key] = value
        user_usages = {}
        if usages:
            user_usages = self._get_usages(context, resources, project_id,
                                           user_id=user_id)
        return self._process_quotas(context, resources, project_id,
                                    user_quotas, quota_class,
                                    defaults=defaults, usages=user_usages)

    def get_project_quotas(self, context, resources, project_id,
                           quota_class=None, defaults=True,
                           usages=True, remains=False, project_quotas=None):
        """Given a list of resources, retrieve the quotas for the given
        project.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param project_id: The ID of the project to return quotas for.
        :param quota_class: If project_id != context.project_id, the
                            quota class cannot be determined.  This
                            parameter allows it to be specified.  It
                            will be ignored if project_id ==
                            context.project_id.
        :param defaults: If True, the quota class value (or the
                         default value, if there is no value from the
                         quota class) will be reported if there is no
                         specific value for the resource.
        :param usages: If True, the current counts will also be returned.
        :param remains: If True, the current remains of the project will
                        will be returned.
        :param project_quotas: Quotas dictionary for the specified project.
        """
        project_quotas = project_quotas or objects.Quotas.get_all_by_project(
            context, project_id)
        project_usages = {}
        if usages:
            project_usages = self._get_usages(context, resources, project_id)
        return self._process_quotas(context, resources, project_id,
                                    project_quotas, quota_class,
                                    defaults=defaults, usages=project_usages,
                                    remains=remains)

    def _is_unlimited_value(self, v):
        """A helper method to check for unlimited value.
        """

        return v <= self.UNLIMITED_VALUE

    def _sum_quota_values(self, v1, v2):
        """A helper method that handles unlimited values when performing
        sum operation.
        """

        if self._is_unlimited_value(v1) or self._is_unlimited_value(v2):
            return self.UNLIMITED_VALUE
        return v1 + v2

    def _sub_quota_values(self, v1, v2):
        """A helper method that handles unlimited values when performing
        subtraction operation.
        """

        if self._is_unlimited_value(v1) or self._is_unlimited_value(v2):
            return self.UNLIMITED_VALUE
        return v1 - v2

    def get_settable_quotas(self, context, resources, project_id,
                            user_id=None):
        """Given a list of resources, retrieve the range of settable quotas for
        the given user or project.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param project_id: The ID of the project to return quotas for.
        :param user_id: The ID of the user to return quotas for.
        """

        settable_quotas = {}
        db_proj_quotas = objects.Quotas.get_all_by_project(context, project_id)
        project_quotas = self.get_project_quotas(context, resources,
                                                 project_id, remains=True,
                                                 project_quotas=db_proj_quotas)
        if user_id:
            setted_quotas = objects.Quotas.get_all_by_project_and_user(
                context, project_id, user_id)
            user_quotas = self.get_user_quotas(context, resources,
                                               project_id, user_id,
                                               project_quotas=db_proj_quotas,
                                               user_quotas=setted_quotas)
            for key, value in user_quotas.items():
                # Maximum is the remaining quota for a project (class/default
                # minus the sum of all user quotas in the project), plus the
                # given user's quota. So if the class/default is 20 and there
                # are two users each with quota of 5, then there is quota of
                # 10 remaining. The given user currently has quota of 5, so
                # the maximum you could update their quota to would be 15.
                # Class/default 20 - currently used in project 10 + current
                # user 5 = 15.
                maximum = \
                    self._sum_quota_values(project_quotas[key]['remains'],
                                           setted_quotas.get(key, 0))
                # This function is called for the quota_sets api and the
                # corresponding nova-manage command. The idea is when someone
                # attempts to update a quota, the value chosen must be at least
                # as much as the current usage and less than or equal to the
                # project limit less the sum of existing per user limits.
                minimum = value['in_use']
                settable_quotas[key] = {'minimum': minimum, 'maximum': maximum}
        else:
            for key, value in project_quotas.items():
                minimum = \
                    max(int(self._sub_quota_values(value['limit'],
                                                   value['remains'])),
                        int(value['in_use']))
                settable_quotas[key] = {'minimum': minimum, 'maximum': -1}
        return settable_quotas

    def _get_syncable_resources(self, resources, user_id=None):
        """Given a list of resources, retrieve the syncable resources
        scoped to a project or a user.

        A resource is syncable if it has a function to sync the quota
        usage record with the actual usage of the project or user.

        :param resources: A dictionary of the registered resources.
        :param user_id: Optional. If user_id is specified, user-scoped
                        resources will be returned. Otherwise,
                        project-scoped resources will be returned.
        :returns: A list of resource names scoped to a project or
                  user that can be sync'd.
        """
        syncable_resources = []
        per_project_resources = db.quota_get_per_project_resources()
        for key, value in resources.items():
            if isinstance(value, ReservableResource):
                # Resources are either project-scoped or user-scoped
                project_scoped = (user_id is None and
                                  key in per_project_resources)
                user_scoped = (user_id is not None and
                               key not in per_project_resources)
                if project_scoped or user_scoped:
                    syncable_resources.append(key)
        return syncable_resources

    def _get_quotas(self, context, resources, keys, project_id=None,
                    user_id=None, project_quotas=None):
        """A helper method which retrieves the quotas for the specific
        resources identified by keys, and which apply to the current
        context.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param keys: A list of the desired quotas to retrieve.
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        :param user_id: Specify the user_id if current context
                        is admin and admin wants to impact on
                        common user.
        :param project_quotas: Quotas dictionary for the specified project.
        """

        # Filter resources
        desired = set(keys)
        sub_resources = {k: v for k, v in resources.items() if k in desired}

        # Make sure we accounted for all of them...
        if len(keys) != len(sub_resources):
            unknown = desired - set(sub_resources.keys())
            raise exception.QuotaResourceUnknown(unknown=sorted(unknown))

        if user_id:
            LOG.debug('Getting quotas for user %(user_id)s and project '
                      '%(project_id)s. Resources: %(keys)s',
                      {'user_id': user_id, 'project_id': project_id,
                       'keys': keys})
            # Grab and return the quotas (without usages)
            quotas = self.get_user_quotas(context, sub_resources,
                                          project_id, user_id,
                                          context.quota_class, usages=False,
                                          project_quotas=project_quotas)
        else:
            LOG.debug('Getting quotas for project %(project_id)s. Resources: '
                      '%(keys)s', {'project_id': project_id, 'keys': keys})
            # Grab and return the quotas (without usages)
            quotas = self.get_project_quotas(context, sub_resources,
                                             project_id,
                                             context.quota_class,
                                             usages=False,
                                             project_quotas=project_quotas)

        return {k: v['limit'] for k, v in quotas.items()}

    def limit_check(self, context, resources, values, project_id=None,
                    user_id=None):
        """Check simple quota limits.

        For limits--those quotas for which there is no usage
        synchronization function--this method checks that a set of
        proposed values are permitted by the limit restriction.

        This method will raise a QuotaResourceUnknown exception if a
        given resource is unknown or if it is not a simple limit
        resource.

        If any of the proposed values is over the defined quota, an
        OverQuota exception will be raised with the sorted list of the
        resources which are too high.  Otherwise, the method returns
        nothing.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param values: A dictionary of the values to check against the
                       quota.
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        :param user_id: Specify the user_id if current context
                        is admin and admin wants to impact on
                        common user.
        """
        _valid_method_call_check_resources(values, 'check', resources)

        # Ensure no value is less than zero
        unders = [key for key, val in values.items() if val < 0]
        if unders:
            raise exception.InvalidQuotaValue(unders=sorted(unders))

        # If project_id is None, then we use the project_id in context
        if project_id is None:
            project_id = context.project_id
        # If user id is None, then we use the user_id in context
        if user_id is None:
            user_id = context.user_id

        # Get the applicable quotas
        project_quotas = objects.Quotas.get_all_by_project(context, project_id)
        quotas = self._get_quotas(context, resources, values.keys(),
                                  project_id=project_id,
                                  project_quotas=project_quotas)
        user_quotas = self._get_quotas(context, resources, values.keys(),
                                       project_id=project_id,
                                       user_id=user_id,
                                       project_quotas=project_quotas)

        # Check the quotas and construct a list of the resources that
        # would be put over limit by the desired values
        overs = [key for key, val in values.items()
                 if quotas[key] >= 0 and quotas[key] < val or
                 (user_quotas[key] >= 0 and user_quotas[key] < val)]
        if overs:
            headroom = {}
            for key in overs:
                headroom[key] = min(
                    val for val in (quotas.get(key), project_quotas.get(key))
                    if val is not None
                )
            raise exception.OverQuota(overs=sorted(overs), quotas=quotas,
                                      usages={}, headroom=headroom)

    def limit_check_project_and_user(self, context, resources,
                                     project_values=None, user_values=None,
                                     project_id=None, user_id=None):
        """Check values (usage + desired delta) against quota limits.

        For limits--this method checks that a set of
        proposed values are permitted by the limit restriction.

        This method will raise a QuotaResourceUnknown exception if a
        given resource is unknown or if it is not a simple limit
        resource.

        If any of the proposed values is over the defined quota, an
        OverQuota exception will be raised with the sorted list of the
        resources which are too high.  Otherwise, the method returns
        nothing.

        :param context: The request context, for access checks
        :param resources: A dictionary of the registered resources
        :param project_values: Optional dict containing the resource values to
                            check against project quota,
                            e.g. {'instances': 1, 'cores': 2, 'memory_mb': 512}
        :param user_values: Optional dict containing the resource values to
                            check against user quota,
                            e.g. {'instances': 1, 'cores': 2, 'memory_mb': 512}
        :param project_id: Optional project_id for scoping the limit check to a
                           different project than in the context
        :param user_id: Optional user_id for scoping the limit check to a
                        different user than in the context
        """
        if project_values is None:
            project_values = {}
        if user_values is None:
            user_values = {}

        _valid_method_call_check_resources(project_values, 'check', resources)
        _valid_method_call_check_resources(user_values, 'check', resources)

        if not any([project_values, user_values]):
            raise exception.Invalid(
                'Must specify at least one of project_values or user_values '
                'for the limit check.')

        # Ensure no value is less than zero
        for vals in (project_values, user_values):
            unders = [key for key, val in vals.items() if val < 0]
            if unders:
                raise exception.InvalidQuotaValue(unders=sorted(unders))

        # Get a set of all keys for calling _get_quotas() so we get all of the
        # resource limits we need.
        all_keys = set(project_values).union(user_values)

        # Keys that are in both project_values and user_values need to be
        # checked against project quota and user quota, respectively.
        # Keys that are not in both only need to be checked against project
        # quota or user quota, if it is defined. Separate the keys that don't
        # need to be checked against both quotas, merge them into one dict,
        # and remove them from project_values and user_values.
        keys_to_merge = set(project_values).symmetric_difference(user_values)
        merged_values = {}
        for key in keys_to_merge:
            # The key will be either in project_values or user_values based on
            # the earlier symmetric_difference. Default to 0 in case the found
            # value is 0 and won't take precedence over a None default.
            merged_values[key] = (project_values.get(key, 0) or
                                  user_values.get(key, 0))
            project_values.pop(key, None)
            user_values.pop(key, None)

        # If project_id is None, then we use the project_id in context
        if project_id is None:
            project_id = context.project_id
        # If user id is None, then we use the user_id in context
        if user_id is None:
            user_id = context.user_id

        # Get the applicable quotas. They will be merged together (taking the
        # min limit) if project_values and user_values were not specified
        # together.

        # per project quota limits (quotas that have no concept of
        # user-scoping: fixed_ips, networks, floating_ips)
        project_quotas = objects.Quotas.get_all_by_project(context, project_id)
        # per user quotas, project quota limits (for quotas that have
        # user-scoping, limits for the project)
        quotas = self._get_quotas(context, resources, all_keys,
                                  project_id=project_id,
                                  project_quotas=project_quotas)
        # per user quotas, user quota limits (for quotas that have
        # user-scoping, the limits for the user)
        user_quotas = self._get_quotas(context, resources, all_keys,
                                       project_id=project_id,
                                       user_id=user_id,
                                       project_quotas=project_quotas)

        if merged_values:
            # This is for resources that are not counted across a project and
            # must pass both the quota for the project and the quota for the
            # user.
            # Combine per user project quotas and user_quotas for use in the
            # checks, taking the minimum limit between the two.
            merged_quotas = copy.deepcopy(quotas)
            for k, v in user_quotas.items():
                if k in merged_quotas:
                    merged_quotas[k] = min(merged_quotas[k], v)
                else:
                    merged_quotas[k] = v

            # Check the quotas and construct a list of the resources that
            # would be put over limit by the desired values
            overs = [key for key, val in merged_values.items()
                     if merged_quotas[key] >= 0 and merged_quotas[key] < val]
            if overs:
                headroom = {}
                for key in overs:
                    headroom[key] = merged_quotas[key]
                raise exception.OverQuota(overs=sorted(overs),
                                          quotas=merged_quotas, usages={},
                                          headroom=headroom)

        # This is for resources that are counted across a project and
        # across a user (instances, cores, ram, security_groups,
        # server_groups). The project_values must pass the quota for the
        # project and the user_values must pass the quota for the user.
        over_user_quota = False
        overs = []
        for key in user_values.keys():
            # project_values and user_values should contain the same keys or
            # be empty after the keys in the symmetric_difference were removed
            # from both dicts.
            if quotas[key] >= 0 and quotas[key] < project_values[key]:
                overs.append(key)
            elif (user_quotas[key] >= 0 and
                  user_quotas[key] < user_values[key]):
                overs.append(key)
                over_user_quota = True
        if overs:
            quotas_exceeded = user_quotas if over_user_quota else quotas
            headroom = {}
            for key in overs:
                headroom[key] = quotas_exceeded[key]
            raise exception.OverQuota(overs=sorted(overs),
                                      quotas=quotas_exceeded, usages={},
                                      headroom=headroom)

    def reserve(self, context, resources, deltas, expire=None,
                project_id=None, user_id=None):
        """Check quotas and reserve resources.

        For counting quotas--those quotas for which there is a usage
        synchronization function--this method checks quotas against
        current usage and the desired deltas.

        This method will raise a QuotaResourceUnknown exception if a
        given resource is unknown or if it does not have a usage
        synchronization function.

        If any of the proposed values is over the defined quota, an
        OverQuota exception will be raised with the sorted list of the
        resources which are too high.  Otherwise, the method returns a
        list of reservation UUIDs which were created.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param deltas: A dictionary of the proposed delta changes.
        :param expire: An optional parameter specifying an expiration
                       time for the reservations.  If it is a simple
                       number, it is interpreted as a number of
                       seconds and added to the current time; if it is
                       a datetime.timedelta object, it will also be
                       added to the current time.  A datetime.datetime
                       object will be interpreted as the absolute
                       expiration time.  If None is specified, the
                       default expiration time set by
                       --default-reservation-expire will be used (this
                       value will be treated as a number of seconds).
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        :param user_id: Specify the user_id if current context
                        is admin and admin wants to impact on
                        common user.
        """
        _valid_method_call_check_resources(deltas, 'reserve', resources)

        # Set up the reservation expiration
        if expire is None:
            expire = CONF.quota.reservation_expire
        if isinstance(expire, six.integer_types):
            expire = datetime.timedelta(seconds=expire)
        if isinstance(expire, datetime.timedelta):
            expire = timeutils.utcnow() + expire
        if not isinstance(expire, datetime.datetime):
            raise exception.InvalidReservationExpiration(expire=expire)

        # If project_id is None, then we use the project_id in context
        if project_id is None:
            project_id = context.project_id
            LOG.debug('Reserving resources using context.project_id: %s',
                      project_id)
        # If user_id is None, then we use the project_id in context
        if user_id is None:
            user_id = context.user_id
            LOG.debug('Reserving resources using context.user_id: %s',
                      user_id)

        LOG.debug('Attempting to reserve resources for project %(project_id)s '
                  'and user %(user_id)s. Deltas: %(deltas)s',
                  {'project_id': project_id, 'user_id': user_id,
                   'deltas': deltas})

        # Get the applicable quotas.
        # NOTE(Vek): We're not worried about races at this point.
        #            Yes, the admin may be in the process of reducing
        #            quotas, but that's a pretty rare thing.
        project_quotas = objects.Quotas.get_all_by_project(context, project_id)
        LOG.debug('Quota limits for project %(project_id)s: '
                  '%(project_quotas)s', {'project_id': project_id,
                                         'project_quotas': project_quotas})

        quotas = self._get_quotas(context, resources, deltas.keys(),
                                  project_id=project_id,
                                  project_quotas=project_quotas)
        LOG.debug('Quotas for project %(project_id)s after resource sync: '
                  '%(quotas)s', {'project_id': project_id, 'quotas': quotas})
        user_quotas = self._get_quotas(context, resources, deltas.keys(),
                                       project_id=project_id,
                                       user_id=user_id,
                                       project_quotas=project_quotas)
        LOG.debug('Quotas for project %(project_id)s and user %(user_id)s '
                  'after resource sync: %(quotas)s',
                  {'project_id': project_id, 'user_id': user_id,
                   'quotas': user_quotas})

        # NOTE(Vek): Most of the work here has to be done in the DB
        #            API, because we have to do it in a transaction,
        #            which means access to the session.  Since the
        #            session isn't available outside the DBAPI, we
        #            have to do the work there.
        return db.quota_reserve(context, resources, quotas, user_quotas,
                                deltas, expire,
                                CONF.quota.until_refresh, CONF.quota.max_age,
                                project_id=project_id, user_id=user_id)

    def commit(self, context, reservations, project_id=None, user_id=None):
        """Commit reservations.

        :param context: The request context, for access checks.
        :param reservations: A list of the reservation UUIDs, as
                             returned by the reserve() method.
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        :param user_id: Specify the user_id if current context
                        is admin and admin wants to impact on
                        common user.
        """
        # If project_id is None, then we use the project_id in context
        if project_id is None:
            project_id = context.project_id
        # If user_id is None, then we use the user_id in context
        if user_id is None:
            user_id = context.user_id

        db.reservation_commit(context, reservations, project_id=project_id,
                              user_id=user_id)

    def rollback(self, context, reservations, project_id=None, user_id=None):
        """Roll back reservations.

        :param context: The request context, for access checks.
        :param reservations: A list of the reservation UUIDs, as
                             returned by the reserve() method.
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        :param user_id: Specify the user_id if current context
                        is admin and admin wants to impact on
                        common user.
        """
        # If project_id is None, then we use the project_id in context
        if project_id is None:
            project_id = context.project_id
        # If user_id is None, then we use the user_id in context
        if user_id is None:
            user_id = context.user_id

        db.reservation_rollback(context, reservations, project_id=project_id,
                                user_id=user_id)

    def usage_reset(self, context, resources):
        """Reset the usage records for a particular user on a list of
        resources.  This will force that user's usage records to be
        refreshed the next time a reservation is made.

        Note: this does not affect the currently outstanding
        reservations the user has; those reservations must be
        committed or rolled back (or expired).

        :param context: The request context, for access checks.
        :param resources: A list of the resource names for which the
                          usage must be reset.
        """

        # We need an elevated context for the calls to
        # quota_usage_update()
        elevated = context.elevated()

        for resource in resources:
            try:
                # Reset the usage to -1, which will force it to be
                # refreshed
                db.quota_usage_update(elevated, context.project_id,
                                      context.user_id,
                                      resource, in_use=-1)
            except exception.QuotaUsageNotFound:
                # That means it'll be refreshed anyway
                pass

    def usage_refresh(self, context, resources, project_id=None,
                      user_id=None, resource_names=None):
        """Refresh the usage records for a particular project and user
        on a list of resources.  This will force usage records to be
        sync'd immediately to the actual usage.

        This method will raise a QuotaUsageRefreshNotAllowed exception if a
        usage refresh is not allowed on a resource for the given project
        or user.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param project_id: Optional: Project whose resources to
                           refresh.  If not set, then the project_id
                           is taken from the context.
        :param user_id: Optional: User whose resources to refresh.
                        If not set, then the user_id is taken from the
                        context.
        :param resources_names: Optional: A list of the resource names
                                for which the usage must be refreshed.
                                If not specified, then all the usages
                                for the project and user will be refreshed.
        """

        if project_id is None:
            project_id = context.project_id
        if user_id is None:
            user_id = context.user_id

        syncable_resources = self._get_syncable_resources(resources, user_id)

        if resource_names:
            for res_name in resource_names:
                if res_name not in syncable_resources:
                    raise exception.QuotaUsageRefreshNotAllowed(
                                                  resource=res_name,
                                                  project_id=project_id,
                                                  user_id=user_id,
                                                  syncable=syncable_resources)
        else:
            resource_names = syncable_resources

        return db.quota_usage_refresh(context, resources, resource_names,
                                      CONF.quota.until_refresh,
                                      CONF.quota.max_age,
                                      project_id=project_id, user_id=user_id)

    def destroy_all_by_project_and_user(self, context, project_id, user_id):
        """Destroy all quotas associated with a project and user.

        :param context: The request context, for access checks.
        :param project_id: The ID of the project being deleted.
        :param user_id: The ID of the user being deleted.
        """

        objects.Quotas.destroy_all_by_project_and_user(context, project_id,
                                                       user_id)

    def destroy_all_by_project(self, context, project_id):
        """Destroy all quotas associated with a project.

        :param context: The request context, for access checks.
        :param project_id: The ID of the project being deleted.
        """

        objects.Quotas.destroy_all_by_project(context, project_id)

    def expire(self, context):
        """Expire reservations.

        Explores all currently existing reservations and rolls back
        any that have expired.

        :param context: The request context, for access checks.
        """

        db.reservation_expire(context)


class NoopQuotaDriver(object):
    """Driver that turns quotas calls into no-ops and pretends that quotas
    for all resources are unlimited.  This can be used if you do not
    wish to have any quota checking.  For instance, with nova compute
    cells, the parent cell should do quota checking, but the child cell
    should not.
    """

    def get_by_project_and_user(self, context, project_id, user_id, resource):
        """Get a specific quota by project and user."""
        # Unlimited
        return -1

    def get_by_project(self, context, project_id, resource):
        """Get a specific quota by project."""
        # Unlimited
        return -1

    def get_by_class(self, context, quota_class, resource):
        """Get a specific quota by quota class."""
        # Unlimited
        return -1

    def get_defaults(self, context, resources):
        """Given a list of resources, retrieve the default quotas.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        """
        quotas = {}
        for resource in resources.values():
            quotas[resource.name] = -1
        return quotas

    def get_class_quotas(self, context, resources, quota_class,
                         defaults=True):
        """Given a list of resources, retrieve the quotas for the given
        quota class.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param quota_class: The name of the quota class to return
                            quotas for.
        :param defaults: If True, the default value will be reported
                         if there is no specific value for the
                         resource.
        """
        quotas = {}
        for resource in resources.values():
            quotas[resource.name] = -1
        return quotas

    def _get_noop_quotas(self, resources, usages=None, remains=False):
        quotas = {}
        for resource in resources.values():
            quotas[resource.name] = {}
            quotas[resource.name]['limit'] = -1
            if usages:
                quotas[resource.name]['in_use'] = -1
                quotas[resource.name]['reserved'] = -1
            if remains:
                quotas[resource.name]['remains'] = -1
        return quotas

    def get_user_quotas(self, context, resources, project_id, user_id,
                        quota_class=None, defaults=True,
                        usages=True):
        """Given a list of resources, retrieve the quotas for the given
        user and project.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param project_id: The ID of the project to return quotas for.
        :param user_id: The ID of the user to return quotas for.
        :param quota_class: If project_id != context.project_id, the
                            quota class cannot be determined.  This
                            parameter allows it to be specified.  It
                            will be ignored if project_id ==
                            context.project_id.
        :param defaults: If True, the quota class value (or the
                         default value, if there is no value from the
                         quota class) will be reported if there is no
                         specific value for the resource.
        :param usages: If True, the current counts will also be returned.
        """
        return self._get_noop_quotas(resources, usages=usages)

    def get_project_quotas(self, context, resources, project_id,
                           quota_class=None, defaults=True,
                           usages=True, remains=False):
        """Given a list of resources, retrieve the quotas for the given
        project.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param project_id: The ID of the project to return quotas for.
        :param quota_class: If project_id != context.project_id, the
                            quota class cannot be determined.  This
                            parameter allows it to be specified.  It
                            will be ignored if project_id ==
                            context.project_id.
        :param defaults: If True, the quota class value (or the
                         default value, if there is no value from the
                         quota class) will be reported if there is no
                         specific value for the resource.
        :param usages: If True, the current counts will also be returned.
        :param remains: If True, the current remains of the project will
                        will be returned.
        """
        return self._get_noop_quotas(resources, usages=usages, remains=remains)

    def get_settable_quotas(self, context, resources, project_id,
                            user_id=None):
        """Given a list of resources, retrieve the range of settable quotas for
        the given user or project.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param project_id: The ID of the project to return quotas for.
        :param user_id: The ID of the user to return quotas for.
        """
        quotas = {}
        for resource in resources.values():
            quotas[resource.name] = {'minimum': 0, 'maximum': -1}
        return quotas

    def limit_check(self, context, resources, values, project_id=None,
                    user_id=None):
        """Check simple quota limits.

        For limits--those quotas for which there is no usage
        synchronization function--this method checks that a set of
        proposed values are permitted by the limit restriction.

        This method will raise a QuotaResourceUnknown exception if a
        given resource is unknown or if it is not a simple limit
        resource.

        If any of the proposed values is over the defined quota, an
        OverQuota exception will be raised with the sorted list of the
        resources which are too high.  Otherwise, the method returns
        nothing.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param values: A dictionary of the values to check against the
                       quota.
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        :param user_id: Specify the user_id if current context
                        is admin and admin wants to impact on
                        common user.
        """
        pass

    def limit_check_project_and_user(self, context, resources,
                                     project_values=None, user_values=None,
                                     project_id=None, user_id=None):
        """Check values against quota limits.

        For limits--this method checks that a set of
        proposed values are permitted by the limit restriction.

        This method will raise a QuotaResourceUnknown exception if a
        given resource is unknown or if it is not a simple limit
        resource.

        If any of the proposed values is over the defined quota, an
        OverQuota exception will be raised with the sorted list of the
        resources which are too high.  Otherwise, the method returns
        nothing.

        :param context: The request context, for access checks
        :param resources: A dictionary of the registered resources
        :param project_values: Optional dict containing the resource values to
                            check against project quota,
                            e.g. {'instances': 1, 'cores': 2, 'memory_mb': 512}
        :param user_values: Optional dict containing the resource values to
                            check against user quota,
                            e.g. {'instances': 1, 'cores': 2, 'memory_mb': 512}
        :param project_id: Optional project_id for scoping the limit check to a
                           different project than in the context
        :param user_id: Optional user_id for scoping the limit check to a
                        different user than in the context
        """
        pass

    def reserve(self, context, resources, deltas, expire=None,
                project_id=None, user_id=None):
        """Check quotas and reserve resources.

        For counting quotas--those quotas for which there is a usage
        synchronization function--this method checks quotas against
        current usage and the desired deltas.

        This method will raise a QuotaResourceUnknown exception if a
        given resource is unknown or if it does not have a usage
        synchronization function.

        If any of the proposed values is over the defined quota, an
        OverQuota exception will be raised with the sorted list of the
        resources which are too high.  Otherwise, the method returns a
        list of reservation UUIDs which were created.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param deltas: A dictionary of the proposed delta changes.
        :param expire: An optional parameter specifying an expiration
                       time for the reservations.  If it is a simple
                       number, it is interpreted as a number of
                       seconds and added to the current time; if it is
                       a datetime.timedelta object, it will also be
                       added to the current time.  A datetime.datetime
                       object will be interpreted as the absolute
                       expiration time.  If None is specified, the
                       default expiration time set by
                       --default-reservation-expire will be used (this
                       value will be treated as a number of seconds).
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        :param user_id: Specify the user_id if current context
                        is admin and admin wants to impact on
                        common user.
        """
        return []

    def commit(self, context, reservations, project_id=None, user_id=None):
        """Commit reservations.

        :param context: The request context, for access checks.
        :param reservations: A list of the reservation UUIDs, as
                             returned by the reserve() method.
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        :param user_id: Specify the user_id if current context
                        is admin and admin wants to impact on
                        common user.
        """
        pass

    def rollback(self, context, reservations, project_id=None, user_id=None):
        """Roll back reservations.

        :param context: The request context, for access checks.
        :param reservations: A list of the reservation UUIDs, as
                             returned by the reserve() method.
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        :param user_id: Specify the user_id if current context
                        is admin and admin wants to impact on
                        common user.
        """
        pass

    def usage_reset(self, context, resources):
        """Reset the usage records for a particular user on a list of
        resources.  This will force that user's usage records to be
        refreshed the next time a reservation is made.

        Note: this does not affect the currently outstanding
        reservations the user has; those reservations must be
        committed or rolled back (or expired).

        :param context: The request context, for access checks.
        :param resources: A list of the resource names for which the
                          usage must be reset.
        """
        pass

    def usage_refresh(self, context, resources, project_id=None, user_id=None,
                      resource_names=None):
        """Refresh the usage records for a particular project and user
        on a list of resources.  This will force usage records to be
        sync'd immediately to the actual usage.

        This method will raise a QuotaUsageRefreshNotAllowed exception if a
        usage refresh is not allowed on a resource for the given project
        or user.

        :param context: The request context, for access checks.
        :param resources: A dictionary of the registered resources.
        :param project_id: Optional: Project whose resources to
                           refresh.  If not set, then the project_id
                           is taken from the context.
        :param user_id: Optional: User whose resources to refresh.
                        If not set, then the user_id is taken from the
                        context.
        :param resources_names: Optional: A list of the resource names
                                for which the usage must be refreshed.
                                If not specified, then all the usages
                                for the project and user will be refreshed.
        """

        pass

    def destroy_all_by_project_and_user(self, context, project_id, user_id):
        """Destroy all quotas associated with a project and user.

        :param context: The request context, for access checks.
        :param project_id: The ID of the project being deleted.
        :param user_id: The ID of the user being deleted.
        """
        pass

    def destroy_all_by_project(self, context, project_id):
        """Destroy all quotas associated with a project.

        :param context: The request context, for access checks.
        :param project_id: The ID of the project being deleted.
        """
        pass

    def expire(self, context):
        """Expire reservations.

        Explores all currently existing reservations and rolls back
        any that have expired.

        :param context: The request context, for access checks.
        """
        pass


class BaseResource(object):
    """Describe a single resource for quota checking."""

    def __init__(self, name, flag=None):
        """Initializes a Resource.

        :param name: The name of the resource, i.e., "instances".
        :param flag: The name of the flag or configuration option
                     which specifies the default value of the quota
                     for this resource.
        """

        self.name = name
        self.flag = flag

    def quota(self, driver, context, **kwargs):
        """Given a driver and context, obtain the quota for this
        resource.

        :param driver: A quota driver.
        :param context: The request context.
        :param project_id: The project to obtain the quota value for.
                           If not provided, it is taken from the
                           context.  If it is given as None, no
                           project-specific quota will be searched
                           for.
        :param quota_class: The quota class corresponding to the
                            project, or for which the quota is to be
                            looked up.  If not provided, it is taken
                            from the context.  If it is given as None,
                            no quota class-specific quota will be
                            searched for.  Note that the quota class
                            defaults to the value in the context,
                            which may not correspond to the project if
                            project_id is not the same as the one in
                            the context.
        """

        # Get the project ID
        project_id = kwargs.get('project_id', context.project_id)

        # Ditto for the quota class
        quota_class = kwargs.get('quota_class', context.quota_class)

        # Look up the quota for the project
        if project_id:
            try:
                return driver.get_by_project(context, project_id, self.name)
            except exception.ProjectQuotaNotFound:
                pass

        # Try for the quota class
        if quota_class:
            try:
                return driver.get_by_class(context, quota_class, self.name)
            except exception.QuotaClassNotFound:
                pass

        # OK, return the default
        return self.default

    @property
    def default(self):
        """Return the default value of the quota."""

        # NOTE(mikal): special case for quota_networks, which is an API
        # flag and not a quota flag
        if self.flag == 'quota_networks':
            return CONF[self.flag]

        return CONF.quota[self.flag] if self.flag else -1


class ReservableResource(BaseResource):
    """Describe a reservable resource."""
    valid_method = 'reserve'

    def __init__(self, name, sync, flag=None):
        """Initializes a ReservableResource.

        Reservable resources are those resources which directly
        correspond to objects in the database, i.e., instances,
        cores, etc.

        Usage synchronization function must be associated with each
        object. This function will be called to determine the current
        counts of one or more resources. This association is done in
        database backend.

        The usage synchronization function will be passed three
        arguments: an admin context, the project ID, and an opaque
        session object, which should in turn be passed to the
        underlying database function.  Synchronization functions
        should return a dictionary mapping resource names to the
        current in_use count for those resources; more than one
        resource and resource count may be returned.  Note that
        synchronization functions may be associated with more than one
        ReservableResource.

        :param name: The name of the resource, i.e., "volumes".
        :param sync: A dbapi methods name which returns a dictionary
                     to resynchronize the in_use count for one or more
                     resources, as described above.
        :param flag: The name of the flag or configuration option
                     which specifies the default value of the quota
                     for this resource.
        """
        super(ReservableResource, self).__init__(name, flag=flag)
        self.sync = sync


class AbsoluteResource(BaseResource):
    """Describe a resource that does not correspond to database objects."""
    valid_method = 'check'


class CountableResource(AbsoluteResource):
    """Describe a resource where the counts aren't based solely on the
    project ID.
    """

    def __init__(self, name, count_as_dict, flag=None):
        """Initializes a CountableResource.

        Countable resources are those resources which directly
        correspond to objects in the database, i.e., instances, cores,
        etc., but for which a count by project ID is inappropriate.  A
        CountableResource must be constructed with a counting
        function, which will be called to determine the current counts
        of the resource.

        The counting function will be passed the context, along with
        the extra positional and keyword arguments that are passed to
        Quota.count_as_dict().  It should return a dict specifying the
        count scoped to a project and/or a user.

        Example count of instances, cores, or ram returned as a rollup
        of all the resources since we only want to query the instances
        table once, not multiple times, for each resource.
        Instances, cores, and ram are counted across a project and
        across a user:

            {'project': {'instances': 5, 'cores': 8, 'ram': 4096},
             'user': {'instances': 1, 'cores': 2, 'ram': 512}}

        Example count of server groups keeping a consistent format.
        Server groups are counted across a project and across a user:

            {'project': {'server_groups': 7},
             'user': {'server_groups': 2}}

        Example count of key pairs keeping a consistent format.
        Key pairs are counted across a user only:

            {'user': {'key_pairs': 5}}

        Note that this counting is not performed in a transaction-safe
        manner.  This resource class is a temporary measure to provide
        required functionality, until a better approach to solving
        this problem can be evolved.

        :param name: The name of the resource, i.e., "instances".
        :param count_as_dict: A callable which returns the count of the
                              resource as a dict.  The arguments passed are as
                              described above.
        :param flag: The name of the flag or configuration option
                     which specifies the default value of the quota
                     for this resource.
        """

        super(CountableResource, self).__init__(name, flag=flag)
        self.count_as_dict = count_as_dict


class QuotaEngine(object):
    """Represent the set of recognized quotas."""

    def __init__(self, quota_driver_class=None):
        """Initialize a Quota object."""
        self._resources = {}
        self._driver_cls = quota_driver_class
        self.__driver = None

    @property
    def _driver(self):
        if self.__driver:
            return self.__driver
        if not self._driver_cls:
            self._driver_cls = CONF.quota.driver
        if isinstance(self._driver_cls, six.string_types):
            self._driver_cls = importutils.import_object(self._driver_cls)
        self.__driver = self._driver_cls
        return self.__driver

    def register_resource(self, resource):
        """Register a resource."""

        self._resources[resource.name] = resource

    def register_resources(self, resources):
        """Register a list of resources."""

        for resource in resources:
            self.register_resource(resource)

    def get_by_project_and_user(self, context, project_id, user_id, resource):
        """Get a specific quota by project and user."""

        return self._driver.get_by_project_and_user(context, project_id,
                                                    user_id, resource)

    def get_by_project(self, context, project_id, resource):
        """Get a specific quota by project."""

        return self._driver.get_by_project(context, project_id, resource)

    def get_by_class(self, context, quota_class, resource):
        """Get a specific quota by quota class."""

        return self._driver.get_by_class(context, quota_class, resource)

    def get_defaults(self, context):
        """Retrieve the default quotas.

        :param context: The request context, for access checks.
        """

        return self._driver.get_defaults(context, self._resources)

    def get_class_quotas(self, context, quota_class, defaults=True):
        """Retrieve the quotas for the given quota class.

        :param context: The request context, for access checks.
        :param quota_class: The name of the quota class to return
                            quotas for.
        :param defaults: If True, the default value will be reported
                         if there is no specific value for the
                         resource.
        """

        return self._driver.get_class_quotas(context, self._resources,
                                             quota_class, defaults=defaults)

    def get_user_quotas(self, context, project_id, user_id, quota_class=None,
                        defaults=True, usages=True):
        """Retrieve the quotas for the given user and project.

        :param context: The request context, for access checks.
        :param project_id: The ID of the project to return quotas for.
        :param user_id: The ID of the user to return quotas for.
        :param quota_class: If project_id != context.project_id, the
                            quota class cannot be determined.  This
                            parameter allows it to be specified.
        :param defaults: If True, the quota class value (or the
                         default value, if there is no value from the
                         quota class) will be reported if there is no
                         specific value for the resource.
        :param usages: If True, the current counts will also be returned.
        """

        return self._driver.get_user_quotas(context, self._resources,
                                            project_id, user_id,
                                            quota_class=quota_class,
                                            defaults=defaults,
                                            usages=usages)

    def get_project_quotas(self, context, project_id, quota_class=None,
                           defaults=True, usages=True, remains=False):
        """Retrieve the quotas for the given project.

        :param context: The request context, for access checks.
        :param project_id: The ID of the project to return quotas for.
        :param quota_class: If project_id != context.project_id, the
                            quota class cannot be determined.  This
                            parameter allows it to be specified.
        :param defaults: If True, the quota class value (or the
                         default value, if there is no value from the
                         quota class) will be reported if there is no
                         specific value for the resource.
        :param usages: If True, the current counts will also be returned.
        :param remains: If True, the current remains of the project will
                        will be returned.
        """

        return self._driver.get_project_quotas(context, self._resources,
                                              project_id,
                                              quota_class=quota_class,
                                              defaults=defaults,
                                              usages=usages,
                                              remains=remains)

    def get_settable_quotas(self, context, project_id, user_id=None):
        """Given a list of resources, retrieve the range of settable quotas for
        the given user or project.

        :param context: The request context, for access checks.
        :param project_id: The ID of the project to return quotas for.
        :param user_id: The ID of the user to return quotas for.
        """

        return self._driver.get_settable_quotas(context, self._resources,
                                                project_id,
                                                user_id=user_id)

    def count_as_dict(self, context, resource, *args, **kwargs):
        """Count a resource and return a dict.

        For countable resources, invokes the count_as_dict() function and
        returns its result.  Arguments following the context and
        resource are passed directly to the count function declared by
        the resource.

        :param context: The request context, for access checks.
        :param resource: The name of the resource, as a string.
        :returns: A dict containing the count(s) for the resource, for example:
                    {'project': {'instances': 2, 'cores': 4, 'ram': 1024},
                     'user': {'instances': 1, 'cores': 2, 'ram': 512}}

                  another example:
                    {'user': {'key_pairs': 5}}
        """

        # Get the resource
        res = self._resources.get(resource)
        if not res or not hasattr(res, 'count_as_dict'):
            raise exception.QuotaResourceUnknown(unknown=[resource])

        return res.count_as_dict(context, *args, **kwargs)

    # TODO(melwitt): This can be removed once no old code can call
    # limit_check(). It will be replaced with limit_check_project_and_user().
    def limit_check(self, context, project_id=None, user_id=None, **values):
        """Check simple quota limits.

        For limits--those quotas for which there is no usage
        synchronization function--this method checks that a set of
        proposed values are permitted by the limit restriction.  The
        values to check are given as keyword arguments, where the key
        identifies the specific quota limit to check, and the value is
        the proposed value.

        This method will raise a QuotaResourceUnknown exception if a
        given resource is unknown or if it is not a simple limit
        resource.

        If any of the proposed values is over the defined quota, an
        OverQuota exception will be raised with the sorted list of the
        resources which are too high.  Otherwise, the method returns
        nothing.

        :param context: The request context, for access checks.
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        :param user_id: Specify the user_id if current context
                        is admin and admin wants to impact on
                        common user.
        """

        return self._driver.limit_check(context, self._resources, values,
                                        project_id=project_id, user_id=user_id)

    def limit_check_project_and_user(self, context, project_values=None,
                                     user_values=None, project_id=None,
                                     user_id=None):
        """Check values against quota limits.

        For limits--this method checks that a set of
        proposed values are permitted by the limit restriction.

        This method will raise a QuotaResourceUnknown exception if a
        given resource is unknown or if it is not a simple limit
        resource.

        If any of the proposed values is over the defined quota, an
        OverQuota exception will be raised with the sorted list of the
        resources which are too high.  Otherwise, the method returns
        nothing.

        :param context: The request context, for access checks
        :param project_values: Optional dict containing the resource values to
                            check against project quota,
                            e.g. {'instances': 1, 'cores': 2, 'memory_mb': 512}
        :param user_values: Optional dict containing the resource values to
                            check against user quota,
                            e.g. {'instances': 1, 'cores': 2, 'memory_mb': 512}
        :param project_id: Optional project_id for scoping the limit check to a
                           different project than in the context
        :param user_id: Optional user_id for scoping the limit check to a
                        different user than in the context
        """
        return self._driver.limit_check_project_and_user(
            context, self._resources, project_values=project_values,
            user_values=user_values, project_id=project_id, user_id=user_id)

    def reserve(self, context, expire=None, project_id=None, user_id=None,
                **deltas):
        """Check quotas and reserve resources.

        For counting quotas--those quotas for which there is a usage
        synchronization function--this method checks quotas against
        current usage and the desired deltas.  The deltas are given as
        keyword arguments, and current usage and other reservations
        are factored into the quota check.

        This method will raise a QuotaResourceUnknown exception if a
        given resource is unknown or if it does not have a usage
        synchronization function.

        If any of the proposed values is over the defined quota, an
        OverQuota exception will be raised with the sorted list of the
        resources which are too high.  Otherwise, the method returns a
        list of reservation UUIDs which were created.

        :param context: The request context, for access checks.
        :param expire: An optional parameter specifying an expiration
                       time for the reservations.  If it is a simple
                       number, it is interpreted as a number of
                       seconds and added to the current time; if it is
                       a datetime.timedelta object, it will also be
                       added to the current time.  A datetime.datetime
                       object will be interpreted as the absolute
                       expiration time.  If None is specified, the
                       default expiration time set by
                       --default-reservation-expire will be used (this
                       value will be treated as a number of seconds).
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        """

        reservations = self._driver.reserve(context, self._resources, deltas,
                                            expire=expire,
                                            project_id=project_id,
                                            user_id=user_id)

        LOG.debug("Created reservations %s", reservations)

        return reservations

    def commit(self, context, reservations, project_id=None, user_id=None):
        """Commit reservations.

        :param context: The request context, for access checks.
        :param reservations: A list of the reservation UUIDs, as
                             returned by the reserve() method.
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        """

        try:
            self._driver.commit(context, reservations, project_id=project_id,
                                user_id=user_id)
        except Exception:
            # NOTE(Vek): Ignoring exceptions here is safe, because the
            # usage resynchronization and the reservation expiration
            # mechanisms will resolve the issue.  The exception is
            # logged, however, because this is less than optimal.
            LOG.exception(_LE("Failed to commit reservations %s"),
                          reservations)
            return
        LOG.debug("Committed reservations %s", reservations)

    def rollback(self, context, reservations, project_id=None, user_id=None):
        """Roll back reservations.

        :param context: The request context, for access checks.
        :param reservations: A list of the reservation UUIDs, as
                             returned by the reserve() method.
        :param project_id: Specify the project_id if current context
                           is admin and admin wants to impact on
                           common user's tenant.
        """

        try:
            self._driver.rollback(context, reservations, project_id=project_id,
                                  user_id=user_id)
        except Exception:
            # NOTE(Vek): Ignoring exceptions here is safe, because the
            # usage resynchronization and the reservation expiration
            # mechanisms will resolve the issue.  The exception is
            # logged, however, because this is less than optimal.
            LOG.exception(_LE("Failed to roll back reservations %s"),
                          reservations)
            return
        LOG.debug("Rolled back reservations %s", reservations)

    def usage_reset(self, context, resources):
        """Reset the usage records for a particular user on a list of
        resources.  This will force that user's usage records to be
        refreshed the next time a reservation is made.

        Note: this does not affect the currently outstanding
        reservations the user has; those reservations must be
        committed or rolled back (or expired).

        :param context: The request context, for access checks.
        :param resources: A list of the resource names for which the
                          usage must be reset.
        """

        self._driver.usage_reset(context, resources)

    def usage_refresh(self, context, project_id=None, user_id=None,
                      resource_names=None):
        """Refresh the usage records for a particular project and user
        on a list of resources.  This will force usage records to be
        sync'd immediately to the actual usage.

        This method will raise a QuotaUsageRefreshNotAllowed exception if a
        usage refresh is not allowed on a resource for the given project
        or user.

        :param context: The request context, for access checks.
        :param project_id: Optional:  Project whose resources to
                           refresh.  If not set, then the project_id
                           is taken from the context.
        :param user_id: Optional: User whose resources to refresh.
                        If not set, then the user_id is taken from the
                        context.
        :param resources_names: Optional: A list of the resource names
                                for which the usage must be refreshed.
                                If not specified, then all the usages
                                for the project and user will be refreshed.
        """

        self._driver.usage_refresh(context, self._resources, project_id,
                                   user_id, resource_names)

    def destroy_all_by_project_and_user(self, context, project_id, user_id):
        """Destroy all quotas, usages, and reservations associated with a
        project and user.

        :param context: The request context, for access checks.
        :param project_id: The ID of the project being deleted.
        :param user_id: The ID of the user being deleted.
        """

        self._driver.destroy_all_by_project_and_user(context,
                                                     project_id, user_id)

    def destroy_all_by_project(self, context, project_id):
        """Destroy all quotas, usages, and reservations associated with a
        project.

        :param context: The request context, for access checks.
        :param project_id: The ID of the project being deleted.
        """

        self._driver.destroy_all_by_project(context, project_id)

    def expire(self, context):
        """Expire reservations.

        Explores all currently existing reservations and rolls back
        any that have expired.

        :param context: The request context, for access checks.
        """

        self._driver.expire(context)

    @property
    def resources(self):
        return sorted(self._resources.keys())


def _keypair_get_count_by_user(context, user_id):
    count = objects.KeyPairList.get_count_by_user(context, user_id)
    return {'user': {'key_pairs': count}}


def _security_group_count(context, project_id, user_id=None):
    """Get the counts of security groups in the database.

    :param context: The request context for database access
    :param project_id: The project_id to count across
    :param user_id: The user_id to count across
    :returns: A dict containing the project-scoped counts and user-scoped
              counts if user_id is specified. For example:

                {'project': {'security_groups': <count across project>},
                 'user': {'security_groups': <count across user>}}
    """
    # NOTE(melwitt): This assumes a single cell.
    return objects.SecurityGroupList.get_counts(context, project_id,
                                                user_id=user_id)


def _server_group_count_members_by_user(context, group, user_id):
    # NOTE(melwitt): This is mostly duplicated from
    # InstanceGroup.count_members_by_user() to query across multiple cells.
    # We need to be able to pass the correct cell context to
    # InstanceList.get_by_filters().
    # TODO(melwitt): Counting across cells for instances means we will miss
    # counting resources if a cell is down. In the future, we should query
    # placement for cores/ram and InstanceMappings for instances (once we are
    # deleting InstanceMappings when we delete instances).
    cell_mappings = objects.CellMappingList.get_all(context)
    greenthreads = []
    filters = {'deleted': False, 'user_id': user_id, 'uuid': group.members}
    for cell_mapping in cell_mappings:
        with nova_context.target_cell(context, cell_mapping) as cctxt:
            greenthreads.append(utils.spawn(
                objects.InstanceList.get_by_filters, cctxt, filters))
    instances = objects.InstanceList(objects=[])
    for greenthread in greenthreads:
        found = greenthread.wait()
        instances = instances + found
    return {'user': {'server_group_members': len(instances)}}


def _fixed_ip_count(context, project_id):
    # NOTE(melwitt): This assumes a single cell.
    count = objects.FixedIPList.get_count_by_project(context, project_id)
    return {'project': {'fixed_ips': count}}


def _floating_ip_count(context, project_id):
    # NOTE(melwitt): This assumes a single cell.
    count = objects.FloatingIPList.get_count_by_project(context, project_id)
    return {'project': {'floating_ips': count}}


def _instances_cores_ram_count(context, project_id, user_id=None):
    """Get the counts of instances, cores, and ram in the database.

    :param context: The request context for database access
    :param project_id: The project_id to count across
    :param user_id: The user_id to count across
    :returns: A dict containing the project-scoped counts and user-scoped
              counts if user_id is specified. For example:

                {'project': {'instances': <count across project>,
                             'cores': <count across project>,
                             'ram': <count across project>},
                 'user': {'instances': <count across user>,
                          'cores': <count across user>,
                          'ram': <count across user>}}
    """
    # TODO(melwitt): Counting across cells for instances means we will miss
    # counting resources if a cell is down. In the future, we should query
    # placement for cores/ram and InstanceMappings for instances (once we are
    # deleting InstanceMappings when we delete instances).
    results = nova_context.scatter_gather_all_cells(
        context, objects.InstanceList.get_counts, project_id, user_id=user_id)
    total_counts = {'project': {'instances': 0, 'cores': 0, 'ram': 0}}
    if user_id:
        total_counts['user'] = {'instances': 0, 'cores': 0, 'ram': 0}
    for cell_uuid, result in results.items():
        if result not in (nova_context.did_not_respond_sentinel,
                          nova_context.raised_exception_sentinel):
            for resource, count in result['project'].items():
                total_counts['project'][resource] += count
            if user_id:
                for resource, count in result['user'].items():
                    total_counts['user'][resource] += count
    return total_counts


def _server_group_count(context, project_id, user_id=None):
    """Get the counts of server groups in the database.

    :param context: The request context for database access
    :param project_id: The project_id to count across
    :param user_id: The user_id to count across
    :returns: A dict containing the project-scoped counts and user-scoped
              counts if user_id is specified. For example:

                {'project': {'server_groups': <count across project>},
                 'user': {'server_groups': <count across user>}}
    """
    return objects.InstanceGroupList.get_counts(context, project_id,
                                                user_id=user_id)


def _security_group_rule_count_by_group(context, security_group_id):
    count = db.security_group_rule_count_by_group(context, security_group_id)
    # NOTE(melwitt): Neither 'project' nor 'user' fit perfectly here as
    # security group rules are counted per security group, not by user or
    # project. But, the quota limits for security_group_rules can be scoped to
    # a user, so we'll use 'user' here.
    return {'user': {'security_group_rules': count}}


QUOTAS = QuotaEngine()


resources = [
    CountableResource('instances', _instances_cores_ram_count, 'instances'),
    CountableResource('cores', _instances_cores_ram_count, 'cores'),
    CountableResource('ram', _instances_cores_ram_count, 'ram'),
    CountableResource('security_groups', _security_group_count,
                      'security_groups'),
    CountableResource('fixed_ips', _fixed_ip_count, 'fixed_ips'),
    CountableResource('floating_ips', _floating_ip_count,
                      'floating_ips'),
    AbsoluteResource('metadata_items', 'metadata_items'),
    AbsoluteResource('injected_files', 'injected_files'),
    AbsoluteResource('injected_file_content_bytes',
                     'injected_file_content_bytes'),
    AbsoluteResource('injected_file_path_bytes',
                     'injected_file_path_length'),
    CountableResource('security_group_rules',
                      _security_group_rule_count_by_group,
                      'security_group_rules'),
    CountableResource('key_pairs', _keypair_get_count_by_user, 'key_pairs'),
    CountableResource('server_groups', _server_group_count, 'server_groups'),
    CountableResource('server_group_members',
                      _server_group_count_members_by_user,
                      'server_group_members'),
    ]


QUOTAS.register_resources(resources)


def _valid_method_call_check_resource(name, method, resources):
    if name not in resources:
        raise exception.InvalidQuotaMethodUsage(method=method, res=name)
    res = resources[name]

    if res.valid_method != method:
        raise exception.InvalidQuotaMethodUsage(method=method, res=name)


def _valid_method_call_check_resources(resource_values, method, resources):
    """A method to check whether the resource can use the quota method.

    :param resource_values: Dict containing the resource names and values
    :param method: The quota method to check
    :param resources: Dict containing Resource objects to validate against
    """

    for name in resource_values.keys():
        _valid_method_call_check_resource(name, method, resources)
