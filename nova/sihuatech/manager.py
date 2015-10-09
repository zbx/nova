# -*- coding: utf-8 -*-
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# Copyright 2011 Justin Santa Barbara
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

"""Handles all processes relating to instances (guest vms).

The :py:class:`ComputeManager` class is a :py:class:`nova.manager.Manager` that
handles RPC calls relating to creating instances.  It is responsible for
building a disk image, launching it via the underlying virtualization driver,
responding to calls to check its state, attaching persistent storage, and
terminating it.

"""

import libvirt
from lxml import etree
import json
from nova.sihuatech.orm import *
from nova.sihuatech.model import Snapshot
from webob.response import Response
import nova.context
from nova.i18n import _
import nova.context
from nova.compute.manager import *
from nova import utils
import os

interval_opts = [
    cfg.IntOpt("update_snapshot_db_interval",
               default=60,
               help=""),
]
CONF = cfg.CONF
CONF.register_opts(interval_opts)


class ComputeManager(nova.compute.manager.ComputeManager):
    """Manages the running instances from creation to destruction."""

    def snapshot_list(self, context, instance):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_list'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshotList = dom.listAllSnapshots();
            result = list()
            for snapshot in snapshotList:
                result.append(self._toDict(snapshot))
            return json.dumps(result, ensure_ascii=False)
        except Exception:
            msg = 'snapshot_list is Failed,instance_id=%s', instance["uuid"]
            LOG.exception(msg, instance=instance)
            raise


    def snapshot_create(self, context, instance, snapshot_name, snapshot_desc=u""):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_create'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            parent_name = ""
            try:
                current_snapshot = dom.snapshotCurrent()
                parent_name = current_snapshot.getName()
            except Exception:
                pass
            # 记录到数据库
            parent_name_str=self._unicode_to_str(parent_name)
            self._save(instance, snapshot_name, snapshot_desc, parent_name_str)
            # 中文必须为str,不能是unicode
            snapshot_name_str = self._unicode_to_str(snapshot_name)
            snapshot_desc_str = self._unicode_to_str(snapshot_desc)
            desc = "<domainsnapshot><name>%s</name><description>%s</description></domainsnapshot>" % (snapshot_name_str, snapshot_desc_str);
            #生成快照xml
            snapshot_xml_file="/tmp/"+uuid.uuid4().hex + '.xml'
            try:
                with open(snapshot_xml_file,'wb') as f:
                    f.write(desc)
                virsh_cmd = ('/usr/bin/virsh', 'snapshot-create', instance['name'],"--xmlfile", snapshot_xml_file)
                utils.execute(*virsh_cmd)
            finally:
                if os.path.exists(snapshot_xml_file):
                    os.remove(snapshot_xml_file)
            snapshot = dom.snapshotLookupByName(snapshot_name_str)
            # 快照创建完成后更改状态
            self._update(instance, snapshot_name, {'state':1})
            LOG.audit(_('snapshot_create success'), context=context, instance=instance)
            return json.dumps(self._toDict(snapshot), ensure_ascii=False)
        except (exception.InstanceNotFound, exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_create is Failed,instance_id=%s,snapshot_name=%', (instance["uuid"], snapshot_name)
            LOG.exception(msg, instance=instance)
            raise

    def snapshot_delete(self, context, instance, snapshot_name):
        context = context.elevated()
        session = Session()
        try:
            LOG.audit(_('snapshot_delete'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            snapshot_name_str = self._unicode_to_str(snapshot_name)
            dom = conn.lookupByName(instance['name'])
            snapshot = dom.snapshotLookupByName(snapshot_name_str);
            snapshot.delete(0);
            self._update(instance, snapshot_name, {'deleted':1})
        except (exception.InstanceNotFound, exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_delete Failed,instance_id=%s,snapshot_name=%', (instance["uuid"], snapshot_name)
            LOG.exception(msg, instance=instance)
            raise
        finally:
            if session:
                session.close()

    def snapshot_revert(self, context, instance, snapshot_name):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_revert'), context=context, instance=instance)
            snapshot_name_str = self._unicode_to_str(snapshot_name)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshot = dom.snapshotLookupByName(snapshot_name_str);
            dom.revertToSnapshot(snapshot, 0)
            LOG.audit(_('snapshot_revert success'), context=context, instance=instance)
            return self._toDict(snapshot)
        except (exception.InstanceNotFound, exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_revert is Failed,instance_id=%s,snapshot_name=%s', (instance["uuid"], snapshot_name)
            LOG.exception(msg, instance=instance)
            raise

    def snapshot_current(self, context, instance):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_current'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshot = dom.snapshotCurrent();
            return json.dumps(self._toDict(snapshot), ensure_ascii=False)
        except (exception.InstanceNotFound, exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_current is Failed,instance_id=%s', instance["uuid"]
            LOG.exception(msg, instance=instance)
            raise
        except (libvirt.libvirtError):
            return ""

    @periodic_task.periodic_task(spacing=CONF.update_snapshot_db_interval)
    def update_snapshot_db(self, context):
        #定时任务,定时把实例的快照信息写到数据库中.
        LOG.audit("update_snapshot_db start...")
        instances = self._get_instances_on_driver(context)
        for instance in instances:
            snapshots=self._snapshot_list(instance)
            for snapshot in snapshots:
                if not self._has_snapshot(instance,snapshot.getName()):
                    self._save_snapshot(instance,snapshot)
                    
    def guest_set_user_password(self, context, instance, user_name, user_password):
        #设置实例密码
        context = context.elevated()
        LOG.audit(_('guest_set_user_password'), context=context, instance=instance)
        user_password = base64.b64encode(user_password)
        args= '''{ "execute": "guest-set-user-password", "arguments": { "crypted": false,"username": "%s","password": "%s" }}''' % (user_name,user_password)
        try:
            cmd = ('virsh', 'qemu-agent-command', instance['name'],args)
            utils.execute(*cmd)
            LOG.info("user_name:%s set password success." % user_name)
            return True
        except Exception:
            msg = 'set_user_password Failed,instance_id=%s', instance["uuid"] 
            LOG.exception(msg, instance=instance)
            raise
##=========================================================================================================
    def _toDict(self, snapshot):
        dict = {}
        dict["name"] = self._getName(snapshot)
        dict["desc"] = self._getDescription(snapshot)
        dict["create_time"] = self._getCreationTime(snapshot)
        try:
            parent = snapshot.getParent();
            dict["parent_name"] = self._getName(parent)
            return dict
        except (libvirt.libvirtError):
            dict["parent_name"] = "-1"
            return dict

    def _getCreationTime(self, snapshot):
        xml_desc = snapshot.getXMLDesc()
        domain = etree.fromstring(xml_desc)
        element = domain.find('creationTime')
        localTime = time.localtime(int(element.text))
        createTime = time.strftime("%Y-%m-%d %H:%M:%S", localTime)
        return createTime

    def _getDescription(self, snapshot):
        xml_desc = snapshot.getXMLDesc()
        domain = etree.fromstring(xml_desc)
        element = domain.find('description')
        if element is not None:
            try:
                return self._str_to_unicode(element.text)
            except Exception:
                return u""
        else:
            return u""
        
    def _getName(self, snapshot):
        snapshot_name = snapshot.getName();
        try:
            snapshot_name = self._str_to_unicode(snapshot_name)
            return snapshot_name
        except Exception:
            return u""
    
    def _save(self, instance, snapshot_name, snapshot_desc, parent_name):
        session = Session()
        try:
            snapshot_record = Snapshot(name=snapshot_name,desc=snapshot_desc,parent=parent_name,instance_uuid=instance["uuid"])
            session.add(snapshot_record)
            session.commit()
        except Exception:
            pass
        finally:
            if session:
                session.close()

    def _save_snapshot(self, instance, snapshot):
        snapshot_name=snapshot.getName()
        snapshot_desc=self._getDescription(snapshot)
        parent_name = ""
        try:
            parent_name=snapshot.getParent().getName()
        except:
            pass
        # 记录到数据库
        session = Session()
        try:
            snapshot_record = Snapshot(name=snapshot_name,desc=snapshot_desc,parent=parent_name,instance_uuid=instance["uuid"],state=1)
            session.add(snapshot_record)
            session.commit()
            LOG.info("save snapshot:%s ,instance:%s" % (self._str_to_unicode(snapshot_name),instance["uuid"]))
        except Exception:
            raise
        finally:
            if session:
                session.close()
        
    def _update(self, instance, snapshot_name, args):
        session = Session()
        try:
            snapshot_record = session.query(Snapshot).filter(Snapshot.deleted == 0,Snapshot.instance_uuid == instance["uuid"],Snapshot.name == snapshot_name).first()
            if snapshot_record:
                if args.has_key("deleted"):
                    snapshot_record.deleted = args["deleted"]
                    snapshot_record.deleted_at = time.strftime("%Y-%m-%d %X", time.localtime())
                if args.has_key("state"):
                    snapshot_record.state = args["state"]
                snapshot_record.updated_at = time.strftime("%Y-%m-%d %X", time.localtime())
                session.add(snapshot_record)
            session.commit()
        except Exception:
            pass
        finally:
            if session:
                session.close()

    def _has_snapshot(self, instance, snapshot_name):
        session = Session()
        try:
            snapshot_record = session.query(Snapshot).filter(Snapshot.deleted == 0,Snapshot.instance_uuid == instance["uuid"],Snapshot.name == snapshot_name).first()
            return snapshot_record
        except Exception:
            raise
        finally:
            if session:
                session.close()

    def _snapshot_list(self, instance):
        try:
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshotList = dom.listAllSnapshots()
            return snapshotList
        except Exception:
            msg = 'snapshot_list is Failed,instance_id=%s', instance["uuid"]
            LOG.exception(msg, instance=instance)
            raise
    
    def _str_to_unicode(self,str):
        #str转为unicode
        try:
            return str.decode('utf-8')
        except Exception:
            return str

    def _unicode_to_str(self,str):
        #unicode转为str
        try:
            return str.encode("utf-8")
        except Exception:
            return str


