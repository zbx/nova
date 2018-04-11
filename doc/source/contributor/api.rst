Extending the API
=================

Background
----------

Nova has v2.1 API frameworks which supports microversions.

This document covers how to add API for the v2.1 API framework. A
:doc:`microversions specific document <microversions>` covers the details
around what is required for the microversions part.

The v2.1 API framework is under ``nova/api`` and each API is implemented in
``nova/api/openstack/compute``.

Note that any change to the Nova API to be merged will first require a
spec be approved first. See `here <https://github.com/openstack/nova-specs>`_
for the appropriate repository. For guidance on the design of the API
please refer to the `OpenStack API WG
<https://wiki.openstack.org/wiki/API_Working_Group>`_


Basic API Controller
--------------------

API controller includes the implementation of API methods for a resource.

A very basic controller of a v2.1 API::

    """Basic Controller"""

    from nova.api.openstack.compute.schemas import xyz
    from nova.api.openstack import extensions
    from nova.api.openstack import wsgi
    from nova.api import validation

    class BasicController(wsgi.Controller):

        # Define support for GET on a collection
        def index(self, req):
            data = {'param': 'val'}
            return data

        # Define support for POST on a collection
        @extensions.expected_errors((400, 409))
        @validation.schema(xyz.create)
        @wsgi.response(201)
        def create(self, req, body):
            write_body_here = ok
            return response_body

        # Defining support for other RESTFul methods based on resouce.


See `servers.py for ref <http://git.openstack.org/cgit/openstack/nova/tree/nova/nova/api/openstack/compute/servers.py>`_.

All of the controller modules should live in the ``nova/api/openstack/compute`` directory.

URL Mapping to API
~~~~~~~~~~~~~~~~~~

The URL mapping is based on the plain list which routes the API request to
appropriate controller and method. Each API needs to add its route information
in ``nova/api/openstack/compute/routes.py``.

A basic skeleton of URL mapping in routers.py::

    """URL Mapping Router List"""

    import functools

    import nova.api.openstack
    from nova.api.openstack.compute import basic_api

    # Create a controller object
    basic_controller = functools.partial(
        _create_controller, basic_api.BasicController, [], [])

    # Routing list structure:
    # (
    #     ('Route path': {
    #         'HTTP method: [
    #             'Controller',
    #             'The method of controller is used to handle this route'
    #         ],
    #         ...
    #     }),
    #     ...
    # )
    ROUTE_LIST = (
        .
        .
        .
        ('/basic', {
            'GET': [basic_controller, 'index'],
            'POST': [basic_controller, 'create']
        }),
        .
        .
        .
    )

Complete routing list can be found in `routes.py <https://git.openstack.org/cgit/openstack/nova/tree/nova/api/openstack/compute/routes.py>`_.


Policy
~~~~~~

Policy (permission) is defined ``etc/nova/policy.json``. Implementation of policy
is changing a bit at the moment. Will add more to this document or reference
another one in the future. Also look at the authorize call in controller currently merged.

Modularity
~~~~~~~~~~

The Nova REST API is separated into different controllers in the directory
'nova/api/openstack/compute/'

Because microversions are supported in the Nova REST API, the API can be
extended without any new controller. But for code readability, the Nova REST API
code still needs modularity. Here are rules for how to separate modules:

* You are adding a new resource
  The new resource should be in standalone module. There isn't any reason to
  put different resources in a single module.

* Add sub-resource for existing resource
  To prevent an existing resource module becoming over-inflated, the
  sub-resource should be implemented in a separate module.

* Add extended attributes for existing resource
  In normally, the extended attributes is part of existing resource's data
  model too. So this can be added into existing resource module directly and
  lightly.
  To avoid namespace complexity, we should avoid to add extended attributes
  in existing extended models. New extended attributes needn't any namespace
  prefix anymore.

JSON-Schema
~~~~~~~~~~~

The v2.1 API validates a REST request body with JSON-Schema library.
Valid body formats are defined with JSON-Schema in the directory
'nova/api/openstack/compute/schemas'. Each definition is used at the
corresponding method with the ``validation.schema`` decorator like::

    @validation.schema(schema.update_something)
    def update(self, req, id, body):
        ....

Similarly to controller modularity, JSON-Schema definitions can be added
in same or separate JSON-Schema module.

The following are the combinations of extensible API and method name
which returns additional JSON-Schema parameters:

* Create a server API  - get_server_create_schema()

For example, keypairs extension(Keypairs class) contains the method
get_server_create_schema() which returns::

    {
        'key_name': parameter_types.name,
    }

then the parameter key_name is allowed on Create a server API.

.. note:: Currently only create schema are implemented in modular way.
          Final goal is to merge them all and define the concluded
          process in this doc.

These are essentially hooks into the servers controller which allow other
controller to modify behaviour without having to modify servers.py. In
the past not having this capability led to very large chunks of
unrelated code being added to servers.py which was difficult to
maintain.


Unit Tests
----------

Should write something more here. But you need to have
both unit and functional tests.


Functional tests and API Samples
--------------------------------

Should write something here

Commit message tags
-------------------

Please ensure you add the ``DocImpact`` tag along with a short
description for any API change.
