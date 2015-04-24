import webob
from nova.api.openstack import common
from nova import compute
from webob import exc
from nova import db
from nova import exception
from nova.virt import fake
from nova.api.openstack import extensions
from nova.openstack.common import log as logging

authorize = extensions.extension_authorizer('compute', 'spice')
import json
LOG = logging.getLogger(__name__)

class SpiceController(object):
         """the Spice API Controller declearation"""
         
         
 
         def index(self, req):
            return None 
        
         def create(self, req):
             return None
 
         def show(self, req, id):
             
             from nova.compute import rpcapi as compute_rpcapi
             self.compute_rpcapi = compute_rpcapi.ComputeAPI()
             
             context = req.environ['nova.context']
             authorize(context)
             instance = common.get_instance(compute.API(), context, id, want_objects=True)
             spice_dict = self.compute_rpcapi.get_spice_console(context,
                            instance=instance, console_type='spice')

             
             return spice_dict['access_url']
         
         def update(self, req):
             return None
 
         def delete(self, req, id):
             return None
         
class Spice(extensions.ExtensionDescriptor):
         """Spice ExtensionDescriptor implementation"""
 
         name = "spice"
         alias = "spice"
         namespace = "www.www.com"
         updated = "2015-04-14 00:00:01"
 
         def get_resources(self):
             """register the new Spice Restful resource"""
 
             resources = [extensions.ResourceExtension('spice', SpiceController())]
             return resources
