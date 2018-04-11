REST API Version History
========================

This documents the changes made to the REST API with every
microversion change. The description for each version should be a
verbose one which has enough information to be suitable for use in
user documentation.

2.1
---

This is the initial version of the v2.1 API which supports
microversions. The V2.1 API is from the REST API users's point of
view exactly the same as v2.0 except with strong input validation.

A user can specify a header in the API request::

  X-OpenStack-Nova-API-Version: <version>

where ``<version>`` is any valid api version for this API.

If no version is specified then the API will behave as if a version
request of v2.1 was requested.

2.2
---

Added Keypair type.

A user can request the creation of a certain 'type' of keypair (``ssh`` or ``x509``)
in the ``os-keypairs`` plugin

If no keypair type is specified, then the default ``ssh`` type of keypair is
created.

Fixes status code for ``os-keypairs`` create method from 200 to 201

Fixes status code for ``os-keypairs`` delete method from 202 to 204

2.3 (Maximum in Kilo)
---------------------

Exposed additional attributes in ``os-extended-server-attributes``:
``reservation_id``, ``launch_index``, ``ramdisk_id``, ``kernel_id``, ``hostname``,
``root_device_name``, ``userdata``.

Exposed ``delete_on_termination`` for ``volumes_attached`` in ``os-extended-volumes``.

This change is required for the extraction of EC2 API into a standalone
service. It exposes necessary properties absent in public nova APIs yet.
Add info for Standalone EC2 API to cut access to Nova DB.

2.4
---

Show the ``reserved`` status on a ``FixedIP`` object in the ``os-fixed-ips`` API
extension. The extension allows one to ``reserve`` and ``unreserve`` a fixed IP
but the show method does not report the current status.

2.5
---

Before version 2.5, the command ``nova list --ip6 xxx`` returns all servers
for non-admins, as the filter option is silently discarded. There is no
reason to treat ip6 different from ip, though, so we just add this
option to the allowed list.

2.6
---

A new API for getting remote console is added::

  POST /servers/<uuid>/remote-consoles
  {
    "remote_console": {
      "protocol": ["vnc"|"rdp"|"serial"|"spice"],
      "type": ["novnc"|"xpvnc"|"rdp-html5"|"spice-html5"|"serial"]
    }
  }

Example response::

  {
    "remote_console": {
      "protocol": "vnc",
      "type": "novnc",
      "url": "http://example.com:6080/vnc_auto.html?token=XYZ"
    }
  }

The old APIs 'os-getVNCConsole', 'os-getSPICEConsole', 'os-getSerialConsole'
and 'os-getRDPConsole' are removed.

2.7
---

Check the ``is_public`` attribute of a flavor before adding tenant access
to it. Reject the request with HTTPConflict error.

2.8
---
Add 'mks' protocol and 'webmks' type for remote consoles.

2.9
---

Add a new ``locked`` attribute to the detailed view, update,
and rebuild action. ``locked`` will be ``true`` if anyone is currently holding
a lock on the server, ``false`` otherwise.

2.10
----

Added user_id parameter to os-keypairs plugin, as well as a new property
in the request body, for the create operation.

Administrators will be able to list, get details and delete keypairs owned by
users other than themselves and to create new keypairs on behalf of their
users.

2.11
----

Exposed attribute ``forced_down`` for ``os-services``.
Added ability to change the ``forced_down`` attribute by calling an update.

2.12 (Maximum in Liberty)
-------------------------

Exposes VIF ``net_id`` attribute in ``os-virtual-interfaces``.
User will be able to get Virtual Interfaces ``net_id`` in Virtual Interfaces
list and can determine in which network a Virtual Interface is plugged into.

2.13
----

Add information ``project_id`` and ``user_id`` to ``os-server-groups``
API response data.

2.14
----

Remove ``onSharedStorage`` parameter from server's evacuate action. Nova will
automatically detect if the instance is on shared storage.
Also adminPass is removed from the response body. The user can get the
password with the server's os-server-password action.

2.15
----

From this version of the API users can choose 'soft-affinity' and
'soft-anti-affinity' rules too for server-groups.

2.16
----

Exposes new host_status attribute for servers/detail and servers/{server_id}.
Ability to get nova-compute status when querying servers. By default, this is
only exposed to cloud administrators.

2.17
----

Add a new API for triggering crash dump in an instance. Different operation
systems in instance may need different configurations to trigger crash dump.

2.18
----
Establishes a set of routes that makes project_id an optional construct in v2.1.

2.19
----
Allow the user to set and get the server description.
The user will be able to set the description when creating, rebuilding,
or updating a server, and get the description as part of the server details.

2.20
----
From this version of the API user can call detach and attach volumes for
instances which are in shelved and shelved_offloaded state.

2.21
----

The ``os-instance-actions`` API now returns information from deleted
instances.

2.22
----

A new resource servers:migrations added. A new API to force live migration
to complete added::

  POST /servers/<uuid>/migrations/<id>/action
  {
    "force_complete": null
  }

2.23
----

From this version of the API users can get the migration summary list by
index API or the information of a specific migration by get API.
And the old top-level resource `/os-migrations` won't be extended anymore.
Add migration_type for old /os-migrations API, also add ref link to the
/servers/{uuid}/migrations/{id} for it when the migration is an in-progress
live-migration.

2.24
----

A new API call to cancel a running live migration::

  DELETE /servers/<uuid>/migrations/<id>

2.25 (Maximum in Mitaka)
------------------------

Modify input parameter for ``os-migrateLive``. The block_migration will
support 'auto' value, and disk_over_commit flag will be removed.

2.26
----

Added support of server tags.

A user can create, update, delete or check existence of simple string tags
for servers by the os-server-tags plugin.

Tags have the following schema restrictions:

* Tag is a Unicode bytestring no longer than 60 characters.
* Tag is a non-empty string.
* '/' is not allowed to be in a tag name
* Comma is not allowed to be in a tag name in order to simplify requests that
  specify lists of tags
* All other characters are allowed to be in a tag name
* Each server can have up to 50 tags.

The resource point for these operations is /servers/<server_id>/tags

A user can add a single tag to the server by sending PUT request to the
/servers/<server_id>/tags/<tag>

where <tag> is any valid tag name.

A user can replace **all** current server tags to the new set of tags
by sending PUT request to the /servers/<server_id>/tags. New set of tags
must be specified in request body. This set must be in list 'tags'.

A user can remove specified tag from the server by sending DELETE request
to the /servers/<server_id>/tags/<tag>

where <tag> is tag name which user wants to remove.

A user can remove **all** tags from the server by sending DELETE request
to the /servers/<server_id>/tags

A user can get a set of server tags with information about server by sending
GET request to the /servers/<server_id>

Request returns dictionary with information about specified server, including
list 'tags' ::

    {
        'id': {server_id},
        ...
        'tags': ['foo', 'bar', 'baz']
    }

A user can get **only** a set of server tags by sending GET request to the
/servers/<server_id>/tags

Response ::

    {
       'tags': ['foo', 'bar', 'baz']
    }

A user can check if a tag exists or not on a server by sending
GET /servers/{server_id}/tags/{tag}

Request returns `204 No Content` if tag exist on a server or `404 Not Found`
if tag doesn't exist on a server.

A user can filter servers in GET /servers request by new filters:

* tags
* tags-any
* not-tags
* not-tags-any

These filters can be combined. Also user can use more than one string tags
for each filter. In this case string tags for each filter must be separated
by comma: GET /servers?tags=red&tags-any=green,orange

2.27
----

Added support for the new form of microversion headers described in the
`Microversion Specification
<http://specs.openstack.org/openstack/api-wg/guidelines/microversion_specification.html>`_.
Both the original form of header and the new form is supported.

2.28
----

Nova API hypervisor.cpu_info change from string to JSON object.

From this version of the API the hypervisor's 'cpu_info' field will be
will returned as JSON object (not string) by sending GET request
to the /v2.1/os-hypervisors/{hypervisor_id}.

2.29
----

Updates the POST request body for the ``evacuate`` action to include the
optional ``force`` boolean field defaulted to False.
Also changes the evacuate action behaviour when providing a ``host`` string
field by calling the nova scheduler to verify the provided host unless the
``force`` attribute is set.

2.30
----

Updates the POST request body for the ``live-migrate`` action to include the
optional ``force`` boolean field defaulted to False.
Also changes the live-migrate action behaviour when providing a ``host``
string field by calling the nova scheduler to verify the provided host unless
the ``force`` attribute is set.

2.31
----

Fix os-console-auth-tokens to return connection info for all types of tokens,
not just RDP.

2.32
----

Adds an optional, arbitrary 'tag' item to the 'networks' item in the server
boot request body. In addition, every item in the block_device_mapping_v2
array can also have an optional, arbitrary 'tag' item. These tags are used to
identify virtual device metadata, as exposed in the metadata API and on the
config drive. For example, a network interface on the virtual PCI bus tagged
with 'nic1' will appear in the metadata along with its bus (PCI), bus address
(ex: 0000:00:02.0), MAC address, and tag ('nic1').

.. note:: A bug has caused the tag attribute to no longer be accepted for
  networks starting with version 2.37 and for block_device_mapping_v2
  starting with version 2.33. In other words, networks could only be tagged
  between versions 2.32 and 2.36 inclusively and block devices only in
  version 2.32. As of version 2.42 the tag attribute has been restored and
  both networks and block devices can be tagged again.

2.33
----

Support pagination for hypervisor by accepting limit and marker from the GET
API request::

  GET /v2.1/{tenant_id}/os-hypervisors?marker={hypervisor_id}&limit={limit}

In the context of device tagging at server create time, 2.33 also removes the
tag attribute from block_device_mapping_v2. This is a bug that is fixed in
2.42, in which the tag attribute is reintroduced.

2.34
----

Checks in ``os-migrateLive`` before live-migration actually starts are now
made in background. ``os-migrateLive`` is not throwing `400 Bad Request` if
pre-live-migration checks fail.

2.35
----

Added pagination support for keypairs.

Optional parameters 'limit' and 'marker' were added to GET /os-keypairs
request, the default sort_key was changed to 'name' field as ASC order,
the generic request format is::

  GET /os-keypairs?limit={limit}&marker={kp_name}

2.36
----

All the APIs which proxy to another service were deprecated in this version,
also the fping API. Those APIs will return 404 with Microversion 2.36. The
network related quotas and limits are removed from API also. The deprecated
API endpoints as below::

  '/images'
  '/os-networks'
  '/os-tenant-networks'
  '/os-fixed-ips'
  '/os-floating-ips'
  '/os-floating-ips-bulk'
  '/os-floating-ip-pools'
  '/os-floating-ip-dns'
  '/os-security-groups'
  '/os-security-group-rules'
  '/os-security-group-default-rules'
  '/os-volumes'
  '/os-snapshots'
  '/os-baremetal-nodes'
  '/os-fping'

2.37
----

Added support for automatic allocation of networking, also known as "Get Me a
Network". With this microversion, when requesting the creation of a new
server (or servers) the ``networks`` entry in the ``server`` portion of the
request body is required. The ``networks`` object in the request can either
be a list or an enum with values:

#. *none* which means no networking will be allocated for the created
   server(s).
#. *auto* which means either a network that is already available to the
   project will be used, or if one does not exist, will be automatically
   created for the project. Automatic network allocation for a project only
   happens once for a project. Subsequent requests using *auto* for the same
   project will reuse the network that was previously allocated.

Also, the ``uuid`` field in the ``networks`` object in the server create
request is now strictly enforced to be in UUID format.

In the context of device tagging at server create time, 2.37 also removes the
tag attribute from networks. This is a bug that is fixed in 2.42, in which
the tag attribute is reintroduced.

2.38 (Maximum in Newton)
------------------------

Before version 2.38, the command ``nova list --status invalid_status`` was
returning empty list for non admin user and 500 InternalServerError for admin
user. As there are sufficient statuses defined already, any invalid status
should not be accepted. From this version of the API admin as well as non
admin user will get 400 HTTPBadRequest if invalid status is passed to nova
list command.

2.39
----

Deprecates image-metadata proxy API that is just a proxy for Glance API
to operate the image metadata. Also removes the extra quota enforcement with
Nova `metadata` quota (quota checks for 'createImage' and 'createBackup'
actions in Nova were removed). After this version Glance configuration
option `image_property_quota` should be used to control the quota of
image metadatas. Also, removes the `maxImageMeta` field from `os-limits`
API response.

2.40
----

Optional query parameters ``limit`` and ``marker`` were added to the
``os-simple-tenant-usage`` endpoints for pagination. If a limit isn’t
provided, the configurable ``max_limit`` will be used which currently
defaults to 1000.

::

    GET /os-simple-tenant-usage?limit={limit}&marker={instance_uuid}
    GET /os-simple-tenant-usage/{tenant_id}?limit={limit}&marker={instance_uuid}

A tenant’s usage statistics may span multiple pages when the number of
instances exceeds limit, and API consumers will need to stitch together
the aggregate results if they still want totals for all instances in a
specific time window, grouped by tenant.

Older versions of the ``os-simple-tenant-usage`` endpoints will not accept
these new paging query parameters, but they will start to silently limit by
``max_limit`` to encourage the adoption of this new microversion, and
circumvent the existing possibility of DoS-like usage requests when there
are thousands of instances.

2.41
----

The 'uuid' attribute of an aggregate is now returned from calls to the
`/os-aggregates` endpoint. This attribute is auto-generated upon creation of
an aggregate. The `os-aggregates` API resource endpoint remains an
administrator-only API.

2.42 (Maximum in Ocata)
-----------------------

In the context of device tagging at server create time, a bug has caused the
tag attribute to no longer be accepted for networks starting with version
2.37 and for block_device_mapping_v2 starting with version 2.33. Microversion
2.42 restores the tag parameter to both networks and block_device_mapping_v2,
allowing networks and block devices to be tagged again.

2.43
----

The ``os-hosts`` API is deprecated as of the 2.43 microversion. Requests
made with microversion >= 2.43 will result in a 404 error. To list and show
host details, use the ``os-hypervisors`` API. To enable or disable a
service, use the ``os-services`` API. There is no replacement for the
`shutdown`, `startup`, `reboot`, or `maintenance_mode` actions as those are
system-level operations which should be outside of the control of the
compute service.

2.44
----

The following APIs which are considered as proxies of Neutron networking API,
are deprecated and will result in a 404 error response in new Microversion::

  POST /servers/{server_uuid}/action
  {
      "addFixedIp": {...}
  }

  POST /servers/{server_uuid}/action
  {
      "removeFixedIp": {...}
  }

  POST /servers/{server_uuid}/action
  {
      "addFloatingIp": {...}
  }

  POST /servers/{server_uuid}/action
  {
      "removeFloatingIp": {...}
  }

Those server actions can be replaced by calling the Neutron API directly.

The nova-network specific API to query the server's interfaces is
deprecated::

  GET /servers/{server_uuid}/os-virtual-interfaces

To query attached neutron interfaces for a specific server, the API
`GET /servers/{server_uuid}/os-interface` can be used.

2.45
----

The ``createImage`` and ``createBackup`` server action APIs no longer return
a ``Location`` header in the response for the snapshot image, they now return
a json dict in the response body with an ``image_id`` key and uuid value.

2.46
----

The request_id created for every inbound request is now returned in
``X-OpenStack-Request-ID`` in addition to ``X-Compute-Request-ID``
to be consistent with the rest of OpenStack. This is a signaling
only microversion, as these header settings happen well before
microversion processing.

2.47
----

Replace the ``flavor`` name/ref with the actual flavor details from the embedded
flavor object when displaying server details.  Requests made with microversion
>= 2.47 will no longer return the flavor ID/link but instead will return a
subset of the flavor details.  If the user is prevented by policy from
indexing extra-specs, then the ``extra_specs`` field will not be included in the
flavor information.

2.48
----

Before version 2.48, VM diagnostics response was just a 'blob' of data
returned by each hypervisor. From this version VM diagnostics response is
standardized. It has a set of fields which each hypervisor will try to fill.
If a hypervisor driver is unable to provide a specific field then this field
will be reported as 'None'.

2.49
----

Continuing from device role tagging at server create time introduced in
version 2.32 and later fixed in 2.42, microversion 2.49 allows the attachment
of network interfaces and volumes with an optional ``tag`` parameter. This tag
is used to identify the virtual devices in the guest and is exposed in the
metadata API. Because the config drive cannot be updated while the guest is
running, it will only contain metadata of devices that were tagged at boot
time. Any changes made to devices while the instance is running - be it
detaching a tagged device or performing a tagged device attachment - will not
be reflected in the config drive.

Tagged volume attachment is not supported for shelved-offloaded instances.

2.50
----

The ``server_groups`` and ``server_group_members`` keys are exposed in GET & PUT
``os-quota-class-sets`` APIs Response body.
Networks related quotas have been filtered out from os-quota-class. Below quotas
are filtered out and not available in ``os-quota-class-sets`` APIs from this
microversion onwards.

- "fixed_ips"
- "floating_ips"
- "networks",
- "security_group_rules"
- "security_groups"

2.51
----

There are two changes for the 2.51 microversion:

* Add ``volume-extended`` event name to the ``os-server-external-events``
  API. This will be used by the Block Storage service when extending the size
  of an attached volume. This signals the Compute service to perform any
  necessary actions on the compute host or hypervisor to adjust for the new
  volume block device size.
* Expose the ``events`` field in the response body for the
  ``GET /servers/{server_id}/os-instance-actions/{request_id}`` API. This is
  useful for API users to monitor when a volume extend operation completes
  for the given server instance. By default only users with the administrator
  role will be able to see event ``traceback`` details.

2.52
----

Adds support for applying tags when creating a server. The tag schema is
the same as in the `2.26`_ microversion.

2.53 (Maximum in Pike)
----------------------

**os-services**

Services are now identified by uuid instead of database id to ensure
uniqueness across cells. This microversion brings the following changes:

* ``GET /os-services`` returns a uuid in the ``id`` field of the response
* ``DELETE /os-services/{service_uuid}`` requires a service uuid in the path
* The following APIs have been superseded by
  ``PUT /os-services/{service_uuid}/``:

  * ``PUT /os-services/disable``
  * ``PUT /os-services/disable-log-reason``
  * ``PUT /os-services/enable``
  * ``PUT /os-services/force-down``

  ``PUT /os-services/{service_uuid}`` takes the following fields in the body:

  * ``status`` - can be either "enabled" or "disabled" to enable or disable
    the given service
  * ``disabled_reason`` - specify with status="disabled" to log a reason for
    why the service is disabled
  * ``forced_down`` - boolean indicating if the service was forced down by
    an external service

* ``PUT /os-services/{service_uuid}`` will now return a full service resource
  representation like in a ``GET`` response

**os-hypervisors**

Hypervisors are now identified by uuid instead of database id to ensure
uniqueness across cells. This microversion brings the following changes:

* ``GET /os-hypervisors/{hypervisor_hostname_pattern}/search`` is deprecated
  and replaced with the ``hypervisor_hostname_pattern`` query parameter on
  the ``GET /os-hypervisors`` and ``GET /os-hypervisors/detail`` APIs.
  Paging with ``hypervisor_hostname_pattern`` is not supported.
* ``GET /os-hypervisors/{hypervisor_hostname_pattern}/servers`` is deprecated
  and replaced with the ``with_servers`` query parameter on the
  ``GET /os-hypervisors`` and ``GET /os-hypervisors/detail`` APIs.
* ``GET /os-hypervisors/{hypervisor_id}`` supports the ``with_servers`` query
  parameter to include hosted server details in the response.
* ``GET /os-hypervisors/{hypervisor_id}`` and
  ``GET /os-hypervisors/{hypervisor_id}/uptime`` APIs now take a uuid value
  for the ``{hypervisor_id}`` path parameter.
* The ``GET /os-hypervisors`` and ``GET /os-hypervisors/detail`` APIs will
  now use a uuid marker for paging across cells.
* The following APIs will now return a uuid value for the hypervisor id and
  optionally service id fields in the response:

  * ``GET /os-hypervisors``
  * ``GET /os-hypervisors/detail``
  * ``GET /os-hypervisors/{hypervisor_id}``
  * ``GET /os-hypervisors/{hypervisor_id}/uptime``
