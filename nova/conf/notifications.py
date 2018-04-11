# Copyright (c) 2016 Intel, Inc.
# Copyright (c) 2013 OpenStack Foundation
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

from oslo_config import cfg

notifications_group = cfg.OptGroup(
    name='notifications',
    title='Notifications options',
    help="""
Most of the actions in Nova which manipulate the system state generate
notifications which are posted to the messaging component (e.g. RabbitMQ) and
can be consumed by any service outside the Openstack. More technical details
at http://docs.openstack.org/developer/nova/notifications.html
""")

ALL_OPTS = [
    cfg.StrOpt(
        'notify_on_state_change',
        choices=(None, 'vm_state', 'vm_and_task_state'),
        deprecated_group='DEFAULT',
        help="""
If set, send compute.instance.update notifications on instance state
changes.

Please refer to https://wiki.openstack.org/wiki/SystemUsageData for
additional information on notifications.

Possible values:

* None - no notifications
* "vm_state" - notifications on VM state changes
* "vm_and_task_state" - notifications on VM and task state changes
"""),

    cfg.BoolOpt(
        'notify_on_api_faults',
        default=False,
        deprecated_group='DEFAULT',
        deprecated_name='notify_api_faults',
        help="""
If enabled, send api.fault notifications on caught exceptions in the
API service.
"""),

    cfg.StrOpt(
        'default_level',
        default='INFO',
        choices=('DEBUG', 'INFO', 'WARN', 'ERROR', 'CRITICAL'),
        deprecated_group='DEFAULT',
        deprecated_name='default_notification_level',
        help="Default notification level for outgoing notifications."),

    cfg.StrOpt(
        'default_publisher_id',
        default='$my_ip',
        deprecated_group='DEFAULT',
        help="""
Default publisher_id for outgoing notifications. If you consider routing
notifications using different publisher, change this value accordingly.

Possible values:

* Defaults to the IPv4 address of this host, but it can be any valid
  oslo.messaging publisher_id

Related options:

*  my_ip - IP address of this host
"""),
    cfg.StrOpt(
        'notification_format',
        choices=['unversioned', 'versioned', 'both'],
        default='both',
        deprecated_group='DEFAULT',
        help="""
Specifies which notification format shall be used by nova.

The default value is fine for most deployments and rarely needs to be changed.
This value can be set to 'versioned' once the infrastructure moves closer to
consuming the newer format of notifications. After this occurs, this option
will be removed (possibly in the "P" release).

Possible values:
* unversioned: Only the legacy unversioned notifications are emitted.
* versioned: Only the new versioned notifications are emitted.
* both: Both the legacy unversioned and the new versioned notifications are
  emitted. (Default)

The list of versioned notifications is visible in
http://docs.openstack.org/developer/nova/notifications.html
"""),
    cfg.ListOpt(
        'versioned_notifications_topics',
        default=['versioned_notifications'],
        help="""
Specifies the topics for the versioned notifications issued by nova.

The default value is fine for most deployments and rarely needs to be changed.
However, if you have a third-party service that consumes versioned
notifications, it might be worth getting a topic for that service.
Nova will send a message containing a versioned notification payload to each
topic queue in this list.

The list of versioned notifications is visible in
http://docs.openstack.org/developer/nova/notifications.html
"""),
    cfg.BoolOpt(
        'bdms_in_notifications',
        default=False,
        help="""
If enabled, include block device information in the versioned notification
payload. Sending block device information is disabled by default as providing
that information can incur some overhead on the system since the information
may need to be loaded from the database.
""")
]


def register_opts(conf):
    conf.register_group(notifications_group)
    conf.register_opts(ALL_OPTS, group=notifications_group)


def list_opts():
    return {notifications_group: ALL_OPTS}
