.. _section_manage-the-cloud:

================
Manage the cloud
================

.. toctree::

   euca2ools.rst
   common/nova-show-usage-statistics-for-hosts-instances.rst

System administrators can use the :command:`openstack` and :command:`euca2ools`
commands to manage their clouds.

The ``openstack`` client and ``euca2ools`` can be used by all users, though
specific commands might be restricted by the Identity service.

**Managing the cloud with the openstack client**

#. The ``python-openstackclient`` package provides an ``openstack`` shell that
   enables Compute API interactions from the command line. Install the client,
   and provide your user name and password (which can be set as environment
   variables for convenience), for the ability to administer the cloud from the
   command line.

   To install python-openstackclient, follow the instructions in the `OpenStack
   User Guide
   <https://docs.openstack.org/user-guide/common/cli-install-openstack-command-line-clients.html>`_.

#. Confirm the installation was successful:

   .. code-block:: console

      $ openstack help
      usage: openstack [--version] [-v | -q] [--log-file LOG_FILE] [-h] [--debug]
                 [--os-cloud <cloud-config-name>]
                 [--os-region-name <auth-region-name>]
                 [--os-cacert <ca-bundle-file>] [--verify | --insecure]
                 [--os-default-domain <auth-domain>]
                 ...

   Running :command:`openstack help` returns a list of ``openstack`` commands
   and parameters. To get help for a subcommand, run:

   .. code-block:: console

      $ openstack help SUBCOMMAND

   For a complete list of ``openstack`` commands and parameters, see the
   `OpenStack Command-Line Reference
   <https://docs.openstack.org/cli-reference/openstack.html>`__.

#. Set the required parameters as environment variables to make running
   commands easier. For example, you can add ``--os-username`` as an
   ``openstack`` option, or set it as an environment variable. To set the user
   name, password, and project as environment variables, use:

   .. code-block:: console

      $ export OS_USERNAME=joecool
      $ export OS_PASSWORD=coolword
      $ export OS_TENANT_NAME=coolu

#. The Identity service gives you an authentication endpoint, which Compute
   recognizes as ``OS_AUTH_URL``:

   .. code-block:: console

      $ export OS_AUTH_URL=http://hostname:5000/v2.0
