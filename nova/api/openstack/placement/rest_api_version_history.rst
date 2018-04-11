REST API Version History
~~~~~~~~~~~~~~~~~~~~~~~~

This documents the changes made to the REST API with every
microversion change. The description for each version should be a
verbose one which has enough information to be suitable for use in
user documentation.

1.0 (Maximum in Newton)
-----------------------

This is the initial version of the placement REST API that was released in
Nova 14.0.0 (Newton). This contains the following routes:

* /resource_providers
* /resource_providers/allocations
* /resource_providers/inventories
* /resource_providers/usages
* /allocations

1.1 Resource provider aggregates
--------------------------------

The 1.1 version adds support for associating aggregates with
resource providers with ``GET`` and ``PUT`` methods on one new
route:

* /resource_providers/{uuid}/aggregates

1.2 Custom resource classes
---------------------------

Placement API version 1.2 adds basic operations allowing an admin to create,
list and delete custom resource classes.

The following new routes are added:

* GET /resource_classes: return all resource classes
* POST /resource_classes: create a new custom resource class
* PUT /resource_classes/{name}: update name of custom resource class
* DELETE /resource_classes/{name}: deletes a custom resource class
* GET /resource_classes/{name}: get a single resource class

Custom resource classes must begin with the prefix "CUSTOM\_" and contain only
the letters A through Z, the numbers 0 through 9 and the underscore "\_"
character.

1.3 member_of query parameter
-----------------------------

Version 1.3 adds support for listing resource providers that are members of
any of the list of aggregates provided using a ``member_of`` query parameter:

* /resource_providers?member_of=in:{agg1_uuid},{agg2_uuid},{agg3_uuid}

1.4 Filter resource providers by requested resource capacity (Maximum in Ocata)
-------------------------------------------------------------------------------

The 1.4 version adds support for querying resource providers that have the
ability to serve a requested set of resources. A new "resources" query string
parameter is now accepted to the `GET /resource_providers` API call. This
parameter indicates the requested amounts of various resources that a provider
must have the capacity to serve. The "resources" query string parameter takes
the form:

``?resources=$RESOURCE_CLASS_NAME:$AMOUNT,$RESOURCE_CLASS_NAME:$AMOUNT``

For instance, if the user wishes to see resource providers that can service a
request for 2 vCPUs, 1024 MB of RAM and 50 GB of disk space, the user can issue
a request to:

`GET /resource_providers?resources=VCPU:2,MEMORY_MB:1024,DISK_GB:50`

If the resource class does not exist, then it will return a HTTP 400.

.. note:: The resources filtering is also based on the `min_unit`, `max_unit`
    and `step_size` of the inventory record. For example, if the `max_unit` is
    512 for the DISK_GB inventory for a particular resource provider and a
    GET request is made for `DISK_GB:1024`, that resource provider will not be
    returned. The `min_unit` is the minimum amount of resource that can be
    requested for a given inventory and resource provider. The `step_size` is
    the increment of resource that can be requested for a given resource on a
    given provider.

1.5 DELETE all inventory for a resource provider
------------------------------------------------

Placement API version 1.5 adds DELETE method for deleting all inventory for a
resource provider. The following new method is supported:

* DELETE /resource_providers/{uuid}/inventories

1.6 Traits API
--------------

The 1.6 version adds basic operations allowing an admin to create, list, and
delete custom traits, also adds basic operations allowing an admin to attach
traits to a resource provider.

The following new routes are added:

* GET /traits: Returns all resource classes.
* PUT /traits/{name}: To insert a single custom trait.
* GET /traits/{name}: To check if a trait name exists.
* DELETE /traits/{name}: To delete the specified trait.
* GET /resource_providers/{uuid}/traits: a list of traits associated
  with a specific resource provider
* PUT /resource_providers/{uuid}/traits: Set all the traits for a
  specific resource provider
* DELETE /resource_providers/{uuid}/traits: Remove any existing trait
  associations for a specific resource provider

Custom traits must begin with the prefix "CUSTOM\_" and contain only
the letters A through Z, the numbers 0 through 9 and the underscore "\_"
character.

1.7 Idempotent PUT /resource_classes/{name}
-------------------------------------------

The 1.7 version changes handling of `PUT /resource_classes/{name}` to be a
create or verification of the resource class with `{name}`. If the resource
class is a custom resource class and does not already exist it will be created
and a ``201`` response code returned. If the class already exists the response
code will be ``204``. This makes it possible to check or create a resource
class in one request.

1.8 Require placement 'project_id', 'user_id' in PUT /allocations
-----------------------------------------------------------------

The 1.8 version adds ``project_id`` and ``user_id`` required request parameters
to ``PUT /allocations``.

1.9 Add GET /usages
--------------------

The 1.9 version adds usages that can be queried by a project or project/user.

The following new routes are added:

``GET /usages?project_id=<project_id>``

   Returns all usages for a given project.

``GET /usages?project_id=<project_id>&user_id=<user_id>``

   Returns all usages for a given project and user.

1.10 Allocation candidates
-------------------------------------------

The 1.10 version brings a new REST resource endpoint for getting a list of
allocation candidates. Allocation candidates are collections of possible
allocations against resource providers that can satisfy a particular request
for resources.
