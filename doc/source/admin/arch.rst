===================
System architecture
===================

OpenStack Compute contains several main components.

- The cloud controller represents the global state and interacts with the
  other components. The ``API server`` acts as the web services front end for
  the cloud controller. The ``compute controller`` provides compute server
  resources and usually also contains the Compute service.

- The ``object store`` is an optional component that provides storage
  services; you can also use OpenStack Object Storage instead.

- An ``auth manager`` provides authentication and authorization services when
  used with the Compute system; you can also use OpenStack Identity as a
  separate authentication service instead.

- A ``volume controller`` provides fast and permanent block-level storage for
  the compute servers.

- The ``network controller`` provides virtual networks to enable compute
  servers to interact with each other and with the public network. You can also
  use OpenStack Networking instead.

- The ``scheduler`` is used to select the most suitable compute controller to
  host an instance.

Compute uses a messaging-based, ``shared nothing`` architecture. All major
components exist on multiple servers, including the compute, volume, and
network controllers, and the Object Storage or Image service.  The state of the
entire system is stored in a database. The cloud controller communicates with
the internal object store using HTTP, but it communicates with the scheduler,
network controller, and volume controller using Advanced Message Queuing
Protocol (AMQP). To avoid blocking a component while waiting for a response,
Compute uses asynchronous calls, with a callback that is triggered when a
response is received.

Hypervisors
~~~~~~~~~~~

Compute controls hypervisors through an API server. Selecting the best
hypervisor to use can be difficult, and you must take budget, resource
constraints, supported features, and required technical specifications into
account. However, the majority of OpenStack development is done on systems
using KVM and Xen-based hypervisors. For a detailed list of features and
support across different hypervisors, see the `Feature Support Matrix
<https://docs.openstack.org/developer/nova/support-matrix.html>`_.

You can also orchestrate clouds using multiple hypervisors in different
availability zones. Compute supports the following hypervisors:

- `Baremetal <https://wiki.openstack.org/wiki/Ironic>`__

- `Docker <https://www.docker.io>`__

- `Hyper-V
  <http://www.microsoft.com/en-us/server-cloud/hyper-v-server/default.aspx>`__

- `Kernel-based Virtual Machine (KVM)
  <http://www.linux-kvm.org/page/Main_Page>`__

- `Linux Containers (LXC) <https://linuxcontainers.org/>`__

- `Quick Emulator (QEMU) <http://wiki.qemu.org/Manual>`__

- `User Mode Linux (UML) <http://user-mode-linux.sourceforge.net/>`__

- `VMware vSphere
  <http://www.vmware.com/products/vsphere-hypervisor/support.html>`__

- `Xen <http://www.xen.org/support/documentation.html>`__

For more information about hypervisors, see the `Hypervisors
<https://docs.openstack.org/ocata/config-reference/compute/hypervisors.html>`__
section in the OpenStack Configuration Reference.

Projects, users, and roles
~~~~~~~~~~~~~~~~~~~~~~~~~~

The Compute system is designed to be used by different consumers in the form of
projects on a shared system, and role-based access assignments.  Roles control
the actions that a user is allowed to perform.

Projects are isolated resource containers that form the principal
organizational structure within the Compute service. They consist of an
individual VLAN, and volumes, instances, images, keys, and users. A user can
specify the project by appending ``project_id`` to their access key.  If no
project is specified in the API request, Compute attempts to use a project with
the same ID as the user.

For projects, you can use quota controls to limit the:

- Number of volumes that can be launched.

- Number of processor cores and the amount of RAM that can be allocated.

- Floating IP addresses assigned to any instance when it launches. This allows
  instances to have the same publicly accessible IP addresses.

- Fixed IP addresses assigned to the same instance when it launches.  This
  allows instances to have the same publicly or privately accessible IP
  addresses.

Roles control the actions a user is allowed to perform. By default, most
actions do not require a particular role, but you can configure them by editing
the ``policy.json`` file for user roles. For example, a rule can be defined so
that a user must have the ``admin`` role in order to be able to allocate a
public IP address.

A project limits users' access to particular images. Each user is assigned a
user name and password. Keypairs granting access to an instance are enabled for
each user, but quotas are set, so that each project can control resource
consumption across available hardware resources.

.. note::

   Earlier versions of OpenStack used the term ``tenant`` instead of
   ``project``. Because of this legacy terminology, some command-line tools use
   ``--tenant_id`` where you would normally expect to enter a project ID.

Block storage
~~~~~~~~~~~~~

OpenStack provides two classes of block storage: ephemeral storage and
persistent volume.

.. rubric:: Ephemeral storage

Ephemeral storage includes a root ephemeral volume and an additional ephemeral
volume.

The root disk is associated with an instance, and exists only for the life of
this very instance. Generally, it is used to store an instance's root file
system, persists across the guest operating system reboots, and is removed on
an instance deletion. The amount of the root ephemeral volume is defined by the
flavor of an instance.

In addition to the ephemeral root volume, all default types of flavors, except
``m1.tiny``, which is the smallest one, provide an additional ephemeral block
device sized between 20 and 160 GB (a configurable value to suit an
environment). It is represented as a raw block device with no partition table
or file system. A cloud-aware operating system can discover, format, and mount
such a storage device. OpenStack Compute defines the default file system for
different operating systems as Ext4 for Linux distributions, VFAT for non-Linux
and non-Windows operating systems, and NTFS for Windows. However, it is
possible to specify any other filesystem type by using ``virt_mkfs`` or
``default_ephemeral_format`` configuration options.

.. note::

   For example, the ``cloud-init`` package included into an Ubuntu's stock
   cloud image, by default, formats this space as an Ext4 file system and
   mounts it on ``/mnt``. This is a cloud-init feature, and is not an OpenStack
   mechanism. OpenStack only provisions the raw storage.

.. rubric:: Persistent volume

A persistent volume is represented by a persistent virtualized block device
independent of any particular instance, and provided by OpenStack Block
Storage.

Only a single configured instance can access a persistent volume.  Multiple
instances cannot access a persistent volume. This type of configuration
requires a traditional network file system to allow multiple instances
accessing the persistent volume. It also requires a traditional network file
system like NFS, CIFS, or a cluster file system such as GlusterFS. These
systems can be built within an OpenStack cluster, or provisioned outside of it,
but OpenStack software does not provide these features.

You can configure a persistent volume as bootable and use it to provide a
persistent virtual instance similar to the traditional non-cloud-based
virtualization system. It is still possible for the resulting instance to keep
ephemeral storage, depending on the flavor selected. In this case, the root
file system can be on the persistent volume, and its state is maintained, even
if the instance is shut down. For more information about this type of
configuration, see `Introduction to the Block Storage service
<https://docs.openstack.org/ocata/config-reference/block-storage/block-storage-overview.html>`_
in the OpenStack Configuration Reference.

.. note::

   A persistent volume does not provide concurrent access from multiple
   instances. That type of configuration requires a traditional network file
   system like NFS, or CIFS, or a cluster file system such as GlusterFS. These
   systems can be built within an OpenStack cluster, or provisioned outside of
   it, but OpenStack software does not provide these features.

EC2 compatibility API
~~~~~~~~~~~~~~~~~~~~~

.. todo:: Does this need to be removed now?

In addition to the native compute API, OpenStack provides an EC2-compatible
API. This API allows EC2 legacy workflows built for EC2 to work with OpenStack.

.. warning::

   Nova in tree EC2-compatible API is deprecated.  The `ec2-api project
   <https://git.openstack.org/cgit/openstack/ec2-api/>`_ is working to
   implement the EC2 API.

You can use numerous third-party tools and language-specific SDKs to interact
with OpenStack clouds. You can use both native and compatibility APIs. Some of
the more popular third-party tools are:

Euca2ools
  A popular open source command-line tool for interacting with the EC2 API.
  This is convenient for multi-cloud environments where EC2 is the common API,
  or for transitioning from EC2-based clouds to OpenStack. For more
  information, see the `Eucalyptus Documentation
  <http://docs.hpcloud.com/eucalyptus>`__.

Hybridfox
  A Firefox browser add-on that provides a graphical interface to many popular
  public and private cloud technologies, including OpenStack.  For more
  information, see the `hybridfox site
  <http://code.google.com/p/hybridfox/>`__.

boto
  Python library for interacting with Amazon Web Services. You can use this
  library to access OpenStack through the EC2 compatibility API.  For more
  information, see the `boto project page on GitHub
  <https://github.com/boto/boto>`__.

fog
  A Ruby cloud services library. It provides methods to interact with a large
  number of cloud and virtualization platforms, including OpenStack. For more
  information, see the `fog site <https://rubygems.org/gems/fog>`__.

php-opencloud
  A PHP SDK designed to work with most OpenStack-based cloud deployments, as
  well as Rackspace public cloud. For more information, see the `php-opencloud
  site <http://www.php-opencloud.com>`__.

Building blocks
~~~~~~~~~~~~~~~

In OpenStack the base operating system is usually copied from an image stored
in the OpenStack Image service. This is the most common case and results in an
ephemeral instance that starts from a known template state and loses all
accumulated states on virtual machine deletion. It is also possible to put an
operating system on a persistent volume in the OpenStack Block Storage volume
system. This gives a more traditional persistent system that accumulates states
which are preserved on the OpenStack Block Storage volume across the deletion
and re-creation of the virtual machine. To get a list of available images on
your system, run:

.. code-block:: console

   $ openstack image list
   +--------------------------------------+-----------------------------+--------+
   | ID                                   | Name                        | Status |
   +--------------------------------------+-----------------------------+--------+
   | aee1d242-730f-431f-88c1-87630c0f07ba | Ubuntu 14.04 cloudimg amd64 | active |
   | 0b27baa1-0ca6-49a7-b3f4-48388e440245 | Ubuntu 14.10 cloudimg amd64 | active |
   | df8d56fc-9cea-4dfd-a8d3-28764de3cb08 | jenkins                     | active |
   +--------------------------------------+-----------------------------+--------+

The displayed image attributes are:

``ID``
  Automatically generated UUID of the image

``Name``
  Free form, human-readable name for image

``Status``
  The status of the image. Images marked ``ACTIVE`` are available for use.

``Server``
  For images that are created as snapshots of running instances, this is the
  UUID of the instance the snapshot derives from. For uploaded images, this
  field is blank.

Virtual hardware templates are called ``flavors``. By default, these are
configurable by admin users, however that behavior can be changed by redefining
the access controls for ``compute_extension:flavormanage`` in
``/etc/nova/policy.json`` on the ``compute-api`` server.

For a list of flavors that are available on your system:

.. code-block:: console

   $ openstack flavor list
   +-----+-----------+-------+------+-----------+-------+-----------+
   | ID  | Name      |   RAM | Disk | Ephemeral | VCPUs | Is_Public |
   +-----+-----------+-------+------+-----------+-------+-----------+
   | 1   | m1.tiny   |   512 |    1 |         0 |     1 | True      |
   | 2   | m1.small  |  2048 |   20 |         0 |     1 | True      |
   | 3   | m1.medium |  4096 |   40 |         0 |     2 | True      |
   | 4   | m1.large  |  8192 |   80 |         0 |     4 | True      |
   | 5   | m1.xlarge | 16384 |  160 |         0 |     8 | True      |
   +-----+-----------+-------+------+-----------+-------+-----------+

Compute service architecture
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

These basic categories describe the service architecture and information about
the cloud controller.

.. rubric:: API server

At the heart of the cloud framework is an API server, which makes command and
control of the hypervisor, storage, and networking programmatically available
to users.

The API endpoints are basic HTTP web services which handle authentication,
authorization, and basic command and control functions using various API
interfaces under the Amazon, Rackspace, and related models. This enables API
compatibility with multiple existing tool sets created for interaction with
offerings from other vendors. This broad compatibility prevents vendor lock-in.

.. rubric:: Message queue

A messaging queue brokers the interaction between compute nodes (processing),
the networking controllers (software which controls network infrastructure),
API endpoints, the scheduler (determines which physical hardware to allocate to
a virtual resource), and similar components. Communication to and from the
cloud controller is handled by HTTP requests through multiple API endpoints.

A typical message passing event begins with the API server receiving a request
from a user. The API server authenticates the user and ensures that they are
permitted to issue the subject command. The availability of objects implicated
in the request is evaluated and, if available, the request is routed to the
queuing engine for the relevant workers.  Workers continually listen to the
queue based on their role, and occasionally their type host name. When an
applicable work request arrives on the queue, the worker takes assignment of
the task and begins executing it. Upon completion, a response is dispatched to
the queue which is received by the API server and relayed to the originating
user.  Database entries are queried, added, or removed as necessary during the
process.

.. rubric:: Compute worker

Compute workers manage computing instances on host machines. The API dispatches
commands to compute workers to complete these tasks:

-  Run instances

-  Delete instances (Terminate instances)

-  Reboot instances

-  Attach volumes

-  Detach volumes

-  Get console output

.. rubric:: Network Controller

The Network Controller manages the networking resources on host machines. The
API server dispatches commands through the message queue, which are
subsequently processed by Network Controllers. Specific operations include:

-  Allocating fixed IP addresses

-  Configuring VLANs for projects

-  Configuring networks for compute nodes
