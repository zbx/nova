.. _section_configuring-compute-migrations:

=========================
Configure live migrations
=========================

Migration enables an administrator to move a virtual machine instance from one
compute host to another. A typical scenario is planned maintenance on the
source host, but migration can also be useful to redistribute the load when
many VM instances are running on a specific physical machine.

This document covers live migrations using the
:ref:`configuring-migrations-kvm-libvirt` and
:ref:`configuring-migrations-xenserver` hypervisors.

.. :ref:`_configuring-migrations-kvm-libvirt`
.. :ref:`_configuring-migrations-xenserver`

.. note::

   Not all Compute service hypervisor drivers support live-migration, or
   support all live-migration features.

   Consult the `Hypervisor Support Matrix
   <https://docs.openstack.org/developer/nova/support-matrix.html>`_ to
   determine which hypervisors support live-migration.

   See the `Hypervisor configuration pages
   <https://docs.openstack.org/ocata/config-reference/compute/hypervisors.html>`_
   for details on hypervisor-specific configuration settings.

The migration types are:

- **Non-live migration**, also known as cold migration or simply migration.

  The instance is shut down, then moved to another hypervisor and restarted.
  The instance recognizes that it was rebooted, and the application running on
  the instance is disrupted.

  This section does not cover cold migration.

- **Live migration**

  The instance keeps running throughout the migration.  This is useful when it
  is not possible or desirable to stop the application running on the instance.

  Live migrations can be classified further by the way they treat instance
  storage:

  - **Shared storage-based live migration**. The instance has ephemeral disks
    that are located on storage shared between the source and destination
    hosts.

  - **Block live migration**, or simply block migration.  The instance has
    ephemeral disks that are not shared between the source and destination
    hosts.  Block migration is incompatible with read-only devices such as
    CD-ROMs and `Configuration Drive (config\_drive)
    <https://docs.openstack.org/user-guide/cli-config-drive.html>`_.

  - **Volume-backed live migration**. Instances use volumes rather than
    ephemeral disks.

  Block live migration requires copying disks from the source to the
  destination host. It takes more time and puts more load on the network.
  Shared-storage and volume-backed live migration does not copy disks.

.. note::

   In a multi-cell cloud, instances can be live migrated to a
   different host in the same cell, but not across cells.

The following sections describe how to configure your hosts for live migrations
using the KVM and XenServer hypervisors.

.. _configuring-migrations-kvm-libvirt:

KVM-libvirt
~~~~~~~~~~~

.. :ref:`_configuring-migrations-kvm-general`
.. :ref:`_configuring-migrations-kvm-block-and-volume-migration`
.. :ref:`_configuring-migrations-kvm-shared-storage`

.. _configuring-migrations-kvm-general:

General configuration
---------------------

To enable any type of live migration, configure the compute hosts according to
the instructions below:

#. Set the following parameters in ``nova.conf`` on all compute hosts:

   - ``vncserver_listen=0.0.0.0``

     You must not make the VNC server listen to the IP address of its compute
     host, since that addresses changes when the instance is migrated.

     .. important::

        Since this setting allows VNC clients from any IP address to connect to
        instance consoles, you must take additional measures like secure
        networks or firewalls to prevent potential attackers from gaining
        access to instances.

   - ``instances_path`` must have the same value for all compute hosts. In
     this guide, the value ``/var/lib/nova/instances`` is assumed.

#. Ensure that name resolution on all compute hosts is identical, so that they
   can connect each other through their hostnames.

   If you use ``/etc/hosts`` for name resolution and enable SELinux, ensure
   that ``/etc/hosts`` has the correct SELinux context:

   .. code-block:: console

      # restorecon /etc/hosts

#. Enable password-less SSH so that root on one compute host can log on to any
   other compute host without providing a password.  The ``libvirtd`` daemon,
   which runs as root, uses the SSH protocol to copy the instance to the
   destination and can't know the passwords of all compute hosts.

   You may, for example, compile root's public SSH keys on all compute hosts
   into an ``authorized_keys`` file and deploy that file to the compute hosts.

#. Configure the firewalls to allow libvirt to communicate between compute
   hosts.

   By default, libvirt uses the TCP port range from 49152 to 49261 for copying
   memory and disk contents. Compute hosts must accept connections in this
   range.

   For information about ports used by libvirt, see the `libvirt documentation
   <http://libvirt.org/remote.html#Remote_libvirtd_configuration>`_.

   .. important::

      Be mindful of the security risks introduced by opening ports.

.. _configuring-migrations-kvm-block-and-volume-migration:

Block migration, volume-based live migration
--------------------------------------------

No additional configuration is required for block migration and volume-backed
live migration.

Be aware that block migration adds load to the network and storage subsystems.

.. _configuring-migrations-kvm-shared-storage:

Shared storage
--------------

Compute hosts have many options for sharing storage, for example NFS, shared
disk array LUNs, Ceph or GlusterFS.

The next steps show how a regular Linux system might be configured as an NFS v4
server for live migration.  For detailed information and alternative ways to
configure NFS on Linux, see instructions for `Ubuntu
<https://help.ubuntu.com/community/SettingUpNFSHowTo>`_, `RHEL and derivatives
<https://access.redhat.com/documentation/en-US/Red_Hat_Enterprise_Linux/7/html/Storage_Administration_Guide/nfs-serverconfig.html>`_
or `SLES and OpenSUSE
<https://www.suse.com/documentation/sles-12/book_sle_admin/data/sec_nfs_configuring-nfs-server.html>`_.

#. Ensure that UID and GID of the nova user are identical on the compute hosts
   and the NFS server.

#. Create a directory with enough disk space for all instances in the cloud,
   owned by user nova. In this guide, we assume ``/var/lib/nova/instances``.

#. Set the execute/search bit on the ``instances`` directory:

   .. code-block:: console

      $ chmod o+x /var/lib/nova/instances

   This  allows qemu to access the ``instances`` directory tree.

#. Export ``/var/lib/nova/instances`` to the compute hosts. For example, add
   the following line to ``/etc/exports``:

   .. code-block:: ini

      /var/lib/nova/instances *(rw,sync,fsid=0,no_root_squash)

   The asterisk permits access to any NFS client. The option ``fsid=0`` exports
   the instances directory as the NFS root.

After setting up the NFS server, mount the remote filesystem on all compute
hosts.

#. Assuming the NFS server's hostname is ``nfs-server``, add this line to
   ``/etc/fstab`` to mount the NFS root:

   .. code-block:: console

      nfs-server:/ /var/lib/nova/instances nfs4 defaults 0 0

#. Test NFS by mounting the instances directory and check access permissions
   for the nova user:

   .. code-block:: console

      $ sudo mount -a -v
      $ ls -ld /var/lib/nova/instances/
      drwxr-xr-x. 2 nova nova 6 Mar 14 21:30 /var/lib/nova/instances/

.. _configuring-migrations-kvm-advanced:

Advanced configuration for KVM and QEMU
---------------------------------------

Live migration copies the instance's memory from the source to the destination
compute host. After a memory page has been copied, the instance may write to it
again, so that it has to be copied again.  Instances that frequently write to
different memory pages can overwhelm the memory copy process and prevent the
live migration from completing.

This section covers configuration settings that can help live migration of
memory-intensive instances succeed.

#. **Live migration completion timeout**

   The Compute service aborts a migration when it has been running for too
   long.  The timeout is calculated based on the instance size, which is the
   instance's memory size in GiB. In the case of block migration, the size of
   ephemeral storage in GiB is added.

   The timeout in seconds is the instance size multiplied by the configurable
   parameter ``live_migration_completion_timeout``, whose default is 800. For
   example, shared-storage live migration of an instance with 8GiB memory will
   time out after 6400 seconds.

#. **Live migration progress timeout**

   The Compute service also aborts a live migration when it detects that memory
   copy is not making progress for a certain time. You can set this time, in
   seconds, through the configurable parameter
   ``live_migration_progress_timeout``.

   In Ocata, the default value of ``live_migration_progress_timeout`` is 0,
   which disables progress timeouts. You should not change this value, since
   the algorithm that detects memory copy progress has been determined to be
   unreliable. It may be re-enabled in future releases.

#. **Instance downtime**

   Near the end of the memory copy, the instance is paused for a short time so
   that the remaining few pages can be copied without interference from
   instance memory writes. The Compute service initializes this time to a small
   value that depends on the instance size, typically around 50 milliseconds.
   When it notices that the memory copy does not make sufficient progress, it
   increases the time gradually.

   You can influence the instance downtime algorithm with the help of three
   configuration variables on the compute hosts:

   .. code-block:: ini

      live_migration_downtime = 500
      live_migration_downtime_steps = 10
      live_migration_downtime_delay = 75

   ``live_migration_downtime`` sets the maximum permitted downtime for a live
   migration, in *milliseconds*.  The default is 500.

   ``live_migration_downtime_steps`` sets the total number of adjustment steps
   until ``live_migration_downtime`` is reached.  The default is 10 steps.

   ``live_migration_downtime_delay`` sets the time interval between two
   adjustment steps in *seconds*. The default is 75.

#. **Auto-convergence**

   One strategy for a successful live migration of a memory-intensive instance
   is slowing the instance down. This is called auto-convergence.  Both libvirt
   and QEMU implement this feature by automatically throttling the instance's
   CPU when memory copy delays are detected.

   Auto-convergence is disabled by default.  You can enable it by setting
   ``live_migration_permit_auto_convergence=true``.

   .. caution::

      Before enabling auto-convergence, make sure that the instance's
      application tolerates a slow-down.

      Be aware that auto-convergence does not guarantee live migration success.

#. **Post-copy**

   Live migration of a memory-intensive instance is certain to succeed when you
   enable post-copy. This feature, implemented by libvirt and QEMU, activates
   the virtual machine on the destination host before all of its memory has
   been copied.  When the virtual machine accesses a page that is missing on
   the destination host, the resulting page fault is resolved by copying the
   page from the source host.

   Post-copy is disabled by default. You can enable it by setting
   ``live_migration_permit_post_copy=true``.

   When you enable both auto-convergence and post-copy, auto-convergence
   remains disabled.

   .. caution::

      The page faults introduced by post-copy can slow the instance down.

      When the network connection between source and destination host is
      interrupted, page faults cannot be resolved anymore and the instance is
      rebooted.

.. TODO Bernd: I *believe* that it is certain to succeed,
.. but perhaps I am missing something.

The full list of live migration configuration parameters is documented in the
`OpenStack Configuration Reference Guide
<https://docs.openstack.org/ocata/config-reference/compute/config-options.html>`_

.. _configuring-migrations-xenserver:

XenServer
~~~~~~~~~

.. :ref:Shared Storage
.. :ref:Block migration

.. _configuring-migrations-xenserver-shared-storage:

Shared storage
--------------

**Prerequisites**

- **Compatible XenServer hypervisors**.

  For more information, see the `Requirements for Creating Resource Pools
  <http://docs.vmd.citrix.com/XenServer/6.0.0/1.0/en_gb/reference.html#pooling_homogeneity_requirements>`_
  section of the XenServer Administrator's Guide.

- **Shared storage**.

  An NFS export, visible to all XenServer hosts.

   .. note::

      For the supported NFS versions, see the `NFS VHD
      <http://docs.vmd.citrix.com/XenServer/6.0.0/1.0/en_gb/reference.html#id1002701>`_
      section of the XenServer Administrator's Guide.

To use shared storage live migration with XenServer hypervisors, the hosts must
be joined to a XenServer pool. To create that pool, a host aggregate must be
created with specific metadata. This metadata is used by the XAPI plug-ins to
establish the pool.

.. rubric:: Using shared storage live migrations with XenServer Hypervisors

#. Add an NFS VHD storage to your master XenServer, and set it as the default
   storage repository. For more information, see NFS VHD in the XenServer
   Administrator's Guide.

#. Configure all compute nodes to use the default storage repository (``sr``)
   for pool operations. Add this line to your ``nova.conf`` configuration files
   on all compute nodes:

   .. code-block:: ini

      sr_matching_filter=default-sr:true

#. Create a host aggregate. This command creates the aggregate, and then
   displays a table that contains the ID of the new aggregate

   .. code-block:: console

      $ openstack aggregate create --zone AVAILABILITY_ZONE POOL_NAME

   Add metadata to the aggregate, to mark it as a hypervisor pool

   .. code-block:: console

      $ openstack aggregate set --property hypervisor_pool=true AGGREGATE_ID

      $ openstack aggregate set --property operational_state=created AGGREGATE_ID

   Make the first compute node part of that aggregate

   .. code-block:: console

      $ openstack aggregate add host AGGREGATE_ID MASTER_COMPUTE_NAME

   The host is now part of a XenServer pool.

#. Add hosts to the pool

   .. code-block:: console

      $ openstack aggregate add host AGGREGATE_ID COMPUTE_HOST_NAME

   .. note::

      The added compute node and the host will shut down to join the host to
      the XenServer pool. The operation will fail if any server other than the
      compute node is running or suspended on the host.

.. _configuring-migrations-xenserver-block-migration:

Block migration
---------------

- **Compatible XenServer hypervisors**.

  The hypervisors must support the Storage XenMotion feature.  See your
  XenServer manual to make sure your edition has this feature.

   .. note::

      - To use block migration, you must use the ``--block-migrate`` parameter
        with the live migration command.

      - Block migration works only with EXT local storage storage repositories,
        and the server must not have any volumes attached.
