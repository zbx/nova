import webob
from nova.api.openstack import common
from nova import compute
from nova.virt.libvirt import driver
from webob import exc
from nova import db
from nova import exception
from nova.virt import fake
from nova.api.openstack import extensions
from nova.openstack.common import log as logging
authorize = extensions.extension_authorizer('compute', 'documents')
LOG = logging.getLogger(__name__)

class SpiceController(object):
         """the Spice API Controller declearation"""
 
         def index(self, req):
            return None 
         def create(self, req):
             return None
 
         def show(self, req, id):
             context = req.environ['nova.context']
             authorize(context)
             instance = common.get_instance(compute.API(), context, id, want_objects=True)
             conn = driver.LibvirtDriver(fake.FakeVirtAPI(), False)
             spice_dict = conn.get_spice_console(context, instance)
             
             return {'spice':{'host':spice_dict.host,'port':spice_dict.port,'tlsPort':spice_dict.tlsPort}}
 
         def update(self, req):
             return None
 
         def delete(self, req, id):
             return webob.Response(status_int=202)
class Spice(extensions.ExtensionDescriptor):
         """Spice ExtensionDescriptor implementation"""
 
         name = "spice"
         alias = "os-spice"
         namespace = "www.www.com"
         updated = "2015-04-14 00:00:01"
 
         def get_resources(self):
             """register the new Spice Restful resource"""
 
             resources = [extensions.ResourceExtension('os-spice',
                 SpiceController())
                 ]
 
             return resources
