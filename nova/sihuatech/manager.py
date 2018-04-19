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
from sqlalchemy import Column, Integer, String, DateTime, Boolean
import datetime
import libvirt
from lxml import etree
import json
from nova.sihuatech.orm import *
from nova.sihuatech.model import Snapshot
import nova.context
from nova.i18n import _
import nova.context
from nova.compute.manager import *
from nova import utils
import os
from nova import objects

interval_opts = [
    cfg.IntOpt("update_snapshot_db_interval",
               default=3600,
               help=""),
]
CONF = cfg.CONF
CONF.register_opts(interval_opts)


class ComputeManager(nova.compute.manager.ComputeManager):
    """Manages the running instances from creation to destruction."""

    def snapshot_list(self, context, instance):
        # 列出所有快照,可以考虑从数据库中取
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
            LOG.exception('snapshot_list is Failed', instance=instance)
            raise

    def snapshot_create(self, context, instance, snapshot_name, snapshot_desc=u""):
        context = context.elevated()
        # 申请快照配额
        quotas = objects.Quotas(context)
        quotas.reserve(context, instance_snapshots=1)
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
            parent_name_str = self._unicode_to_str(parent_name)
            # 中文必须为str,不能是unicode
            snapshot_name_str = self._unicode_to_str(snapshot_name)
            snapshot_desc_str = self._unicode_to_str(snapshot_desc)
            desc = "<domainsnapshot><name>%s</name><description>%s</description></domainsnapshot>" % (snapshot_name_str, snapshot_desc_str);
            # 生成快照xml
            snapshot_xml_file = "/tmp/" + uuid.uuid4().hex + '.xml'
            try:
                with open(snapshot_xml_file, 'wb') as f:
                    f.write(desc)
                virsh_cmd = ('/usr/bin/virsh', 'snapshot-create', instance['name'], "--xmlfile", snapshot_xml_file)
                utils.execute(*virsh_cmd)
            finally:
                if os.path.exists(snapshot_xml_file):
                    os.remove(snapshot_xml_file)
            snapshot = dom.snapshotLookupByName(snapshot_name_str)
            # 快照创建完成后写到数据库
            self._snapshot_create(instance, snapshot_name, snapshot_desc, parent_name_str, snapshot.getXMLDesc(),datetime.datetime.strptime(self._get_creationTime(snapshot), "%Y-%m-%d %H:%M:%S"))
            LOG.audit(_('snapshot_create success'), context=context, instance=instance)
            # 提交配额
            quotas.commit()
            return json.dumps(self._toDict(snapshot), ensure_ascii=False)
        except Exception:
            LOG.exception('snapshot_create is Failed', instance=instance)
            quotas.rollback()
            raise

    def snapshot_delete(self, context, instance, snapshot_name):
        context = context.elevated()
        # 释放配额
        quotas = objects.Quotas(context)
        quotas.reserve(context, instance_snapshots=-1)
        session = Session()
        try:
            LOG.audit(_('snapshot_delete'), context=context, instance=instance)
            conn = libvirt.open('qemu:///system')
            snapshot_name_str = self._unicode_to_str(snapshot_name)
            dom = conn.lookupByName(instance['name'])
            snapshot = dom.snapshotLookupByName(snapshot_name_str);
            snapshot.delete(0);
            # 更新到数据库
            self._snapshot_delete(instance, snapshot_name)
            # 提交配额
            quotas.commit()
        except Exception:
            LOG.exception('snapshot_delete Failed', instance=instance)
            quotas.rollback()
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
            # 更新数据库当前快照的位置
            self._snapshot_revert(instance, snapshot_name_str)
            LOG.audit(_('snapshot_revert success'), context=context, instance=instance)
            return self._toDict(snapshot)
        except (exception.InstanceNotFound, exception.UnexpectedDeletingTaskStateError):
            LOG.exception('snapshot_revert is Failed', instance=instance)
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
            LOG.exception('snapshot_current is Failed', instance=instance)
            raise
        except (libvirt.libvirtError):
            return ""

    @periodic_task.periodic_task(spacing=CONF.update_snapshot_db_interval,run_immediately=True)
    def sync_snapshot_to_db(self, context):
        # 定时任务,定时把实例的快照信息写到数据库中.
        LOG.audit("sync_snapshot_to_db start...")
        instances = self._get_instances_on_driver(context)
        for instance in instances:
            if instance["task_state"] is None:
                with lockutils.lock(instance.uuid,lock_file_prefix='nova-snapshot'):
                    self._sync_snapshot_to_db(instance)
        LOG.audit("sync_snapshot_to_db success")

    def guest_set_user_password(self, context, instance, user_name, user_password):
        # 设置实例密码
        context = context.elevated()
        LOG.audit(_('guest_set_user_password start'), context=context, instance=instance)
        user_password = base64.b64encode(user_password)
        args = '''{ "execute": "guest-set-user-password", "arguments": { "crypted": false,"username": "%s","password": "%s" }}''' % (user_name, user_password)
        try:
            cmd = ('virsh', 'qemu-agent-command', instance['name'], args)
            utils.execute(*cmd)
            LOG.info("user_name:%s set password success." % user_name)
        except Exception:
            LOG.exception('set_user_password Failed', instance=instance)
            raise

    def guest_live_update(self, context, instance, vcpus,memory_size):
        context = context.elevated()
        LOG.audit(_('guest_live_update'), context=context, instance=instance)
        self._guest_set_vcpus(instance,vcpus)
        self._guest_set_memory(instance,memory_size)

        
    def _guest_set_vcpus(self, instance, vcpus):
        LOG.audit(_('_guest_set_vcpus'), instance=instance)
        #设置vcpus
        try:
            cmd = ('virsh', 'qemu-monitor-command', instance['name'], '--hmp','cpu-add %s' % vcpus )
            result=utils.execute(*cmd)
            LOG.info("vcpus:%s,result:%s" % (vcpus,result[0]))

            vcpu_list=self._get_vcpus(instance)
            self._set_vcpu_count(vcpu_list,int(vcpus))
            #virsh qemu-agent-command instance-00000063 '{ "execute": "guest-set-vcpus","arguments":{"vcpus":[{"logical-id":2,"online":false}]}}'
            args = '''{ "execute": "guest-set-vcpus", "arguments": { "vcpus":%s }}''' % json.dumps(vcpu_list)
            cmd = ('virsh', 'qemu-agent-command', instance['name'], args )
            result=utils.execute(*cmd)
            LOG.info("qemu-agent-command cmd:%s" % cmd)
            LOG.info("qemu-agent-command result:%s" ,result[0])
        except Exception:
            LOG.exception('', instance=instance)
            raise
        
    def _guest_set_memory(self, instance,memory_size):
        LOG.audit(_('_guest_set_memory'), instance=instance)
        #设置memory
        try:
            cmd = ('virsh', 'qemu-monitor-command', instance['name'], '--hmp','balloon %s' % memory_size )
            result=utils.execute(*cmd)
            LOG.info("memory_size:%s,result:%s" % (memory_size,result[0]))
        except Exception:
            LOG.exception('', instance=instance)
            raise

    def _get_vcpu_count(self, instance):
        return len(self._get_vcpus(instance))
    
    def _get_vcpus(self, instance):
        args = '''{ "execute": "guest-get-vcpus"}'''
        cmd = ('virsh', 'qemu-agent-command', instance['name'], args )
        str=utils.execute(*cmd)
        obj_json=json.loads(str[0])
        return obj_json['return']
    
    def _set_vcpu_count(self, vcpus,count):
        if vcpus:
            for i in range(0,len(vcpus)):
                cpu=vcpus[i]
                if i<count:
                    cpu["online"]=True
                else:
                    cpu["online"]=False
        


    ##=========================================================================================================

    def _snapshot_list(self, instance):
        try:
            conn = libvirt.open('qemu:///system')
            dom = conn.lookupByName(instance['name'])
            snapshotList = dom.listAllSnapshots()
            return snapshotList
        except Exception:
            LOG.exception('snapshot_list is Failed', instance=instance)
            raise

    def _snapshot_create(self, instance, snapshot_name, snapshot_desc, parent_name, snapshot_xml,created_at):
        session = Session()
        try:
            session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"]).update({'is_current': '0'}, synchronize_session=False)
            snapshot_record = Snapshot(name=snapshot_name, desc=snapshot_desc, parent=parent_name, instance_uuid=instance["uuid"], project_id=instance["project_id"], user_id=instance["user_id"],
                                       is_current='1', xml=snapshot_xml,created_at=created_at)
            session.add(snapshot_record)
            session.commit()
        except Exception:
            raise
        finally:
            if session:
                session.close()

    def _snapshot_revert(self, instance, snapshot_name):
        session = Session()
        try:
            snapshot_current = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"], Snapshot.name == snapshot_name).first()
            if snapshot_current:
                # 重置当前快照位置
                session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"]).update({'is_current': '0'}, synchronize_session=False)
                snapshot_current.is_current = '1'
                session.add(snapshot_current)
                session.commit()
        except Exception:
            raise
        finally:
            if session:
                session.close()

    def _snapshot_delete(self, instance, snapshot_name):
        session = Session()
        try:
            snapshot_record = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"], Snapshot.name == snapshot_name).first()
            if snapshot_record:
                snapshot_record.deleted = '1'
                snapshot_record.deleted_at = time.strftime("%Y-%m-%d %X", time.localtime())
                snapshot_record.updated_at = time.strftime("%Y-%m-%d %X", time.localtime())
                session.add(snapshot_record)
                session.commit()
                # 重新生成父子关系
                self._sync_snapshot_to_db(instance)
        except Exception:
            raise
        finally:
            if session:
                session.close()

    def _toDict(self, snapshot):
        dict = {}
        dict["name"] = self._get_name(snapshot)
        dict["desc"] = self._get_description(snapshot)
        dict["create_time"] = self._get_creationTime(snapshot)
        try:
            parent = snapshot.getParent();
            dict["parent_name"] = self._get_name(parent)
            return dict

        except (libvirt.libvirtError):
            dict["parent_name"] = "-1"
            return dict



    def _get_description(self, snapshot):
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

    def _get_name(self, snapshot):
        snapshot_name = snapshot.getName();
        try:
            snapshot_name = self._str_to_unicode(snapshot_name)
            return snapshot_name
        except Exception:
            return u""

    def _get_parentName(self, snapshot):
        parent_name = ""
        if snapshot:
            try:
                parent_name = snapshot.getParent().getName()
            except:
                pass
        return parent_name


    def _str_to_unicode(self, str):
        # str转为unicode
        try:
            return str.decode('utf-8')
        except Exception:
            return str

    def _unicode_to_str(self, str):
        # unicode转为str
        try:
            return str.encode("utf-8")
        except Exception:
            return str
