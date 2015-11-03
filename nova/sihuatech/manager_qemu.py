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
import nova.context
from nova.i18n import _
import nova.context
from nova.compute.manager import *
from nova import utils
import os
from nova import objects

interval_opts = [
    cfg.IntOpt("update_snapshot_db_interval",
               default=300,
               help=""),
]
CONF = cfg.CONF
CONF.register_opts(interval_opts)


class ComputeManager(nova.compute.manager.ComputeManager):
    """Manages the running instances from creation to destruction."""

    def snapshot_list(self, context, instance):
        context = context.elevated()
        session = Session()
        try:
            snapshot_list = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"])
            result = list()
            for snapshot in snapshot_list:
                result.append(self._toDict(snapshot))
            str = json.dumps(result, ensure_ascii=False)
            return str
        except Exception:
            raise
        finally:
            if session:
                session.close()

    def snapshot_create(self, context, instance, snapshot_name, snapshot_desc=u""):
        context = context.elevated()
        # 申请快照配额
        quotas = objects.Quotas(context)
        quotas.reserve(context, snapshots=1)
        try:
            LOG.audit(_('snapshot_create'), context=context, instance=instance)
            current_snapshot = self._current_snapshot(instance)
            parent_name =""
            if current_snapshot:
                parent_name = current_snapshot.name

            # 记录到数据库
            parent_name_str = self._unicode_to_str(parent_name)
            # 中文必须为str,不能是unicode
            snapshot_name_str = self._unicode_to_str(snapshot_name)
            snapshot_desc_str = self._unicode_to_str(snapshot_desc)
            instance_file = "%s/%s/disk" % (CONF.instances_path,instance["uuid"])
            try:
                virsh_cmd = ('qemu-img', 'snapshot', '-c', snapshot_name_str, instance_file)
                utils.execute(*virsh_cmd)
            except Exception:
                raise
            # 快照创建完成后写到数据库
            self._snapshot_create(instance, snapshot_name, snapshot_desc, parent_name_str)
            LOG.audit(_('snapshot_create success'), context=context, instance=instance)
            # 提交配额
            quotas.commit()
            return json.dumps(self._toDict(self._current_snapshot(instance)), ensure_ascii=False)
        except Exception:
            msg = 'snapshot_create is Failed,instance_id=%s,snapshot_name=%', (instance["uuid"], snapshot_name)
            LOG.exception(msg, instance=instance)
            quotas.rollback()
            raise

    def snapshot_delete(self, context, instance, snapshot_name):
        context = context.elevated()
        # 释放配额
        quotas = objects.Quotas(context)
        quotas.reserve(context, snapshots=-1)
        session = Session()
        try:
            LOG.audit(_('snapshot_delete'), context=context, instance=instance)
            snapshot_name_str = self._unicode_to_str(snapshot_name)
            instance_file = "/var/lib/nova/instance/%s", instance["uuid"]
            try:
                virsh_cmd = ('qemu', 'snapshot', '-d', snapshot_name_str, instance_file)
                utils.execute(*virsh_cmd)
            except Exception:
                raise
            self._snapshot_delete(instance, snapshot_name)
            # 提交配额
            quotas.commit()
        except Exception:
            msg = 'snapshot_delete Failed,instance_id=%s,snapshot_name=%', (instance["uuid"], snapshot_name)
            LOG.exception(msg, instance=instance)
            quotas.rollback()
            raise
        finally:
            if session:
                session.close()

    def snapshot_revert(self, context, instance, snapshot_name):
        context = context.elevated()
        try:
            LOG.audit(_('snapshot_revert'), context=context, instance=instance)           
            # 中文必须为str,不能是unicode
            snapshot_name_str = self._unicode_to_str(snapshot_name)
            instance_file = "%s/%s/disk" % (CONF.instances_path,instance["uuid"])
            try:
                virsh_cmd = ('qemu-img', 'snapshot', '-a', snapshot_name_str, instance_file)
                utils.execute(*virsh_cmd)
            except Exception:
                raise
            snapshot = self._snapshot_revert(instance, snapshot_name_str)
            LOG.audit(_('snapshot_revert success'), context=context, instance=instance)
            return self._toDict(snapshot)
        except (exception.InstanceNotFound, exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_revert is Failed,instance_id=%s,snapshot_name=%s', (instance["uuid"], snapshot_name)
            LOG.exception(msg, instance=instance)
            raise

    def snapshot_current(self, context, instance):
        context = context.elevated()
        try:
            return json.dumps(self._toDict(self._current_snapshot(instance)), ensure_ascii=False)
        except (exception.InstanceNotFound, exception.UnexpectedDeletingTaskStateError):
            msg = 'snapshot_current is Failed,instance_id=%s', instance["uuid"]
            LOG.exception(msg, instance=instance)
            raise
        except (libvirt.libvirtError):
            return ""

    def guest_set_user_password(self, context, instance, user_name, user_password):
        # 设置实例密码
        context = context.elevated()
        LOG.audit(_('guest_set_user_password'), context=context, instance=instance)
        user_password = base64.b64encode(user_password)
        args = '''{ "execute": "guest-set-user-password", "arguments": { "crypted": false,"username": "%s","password": "%s" }}''' % (user_name, user_password)
        try:
            cmd = ('virsh', 'qemu-agent-command', instance['name'], args)
            utils.execute(*cmd)
            LOG.info("user_name:%s set password success." % user_name)
        except Exception:
            msg = 'set_user_password Failed,instance_id=%s', instance["uuid"]
            LOG.exception(msg, instance=instance)
            raise
        ##=========================================================================================================

    def _toDict(self, snapshot):
        if snapshot:
            dict = {}
            dict["name"] = snapshot.name
            dict["desc"] = snapshot.desc
            dict["create_time"] = snapshot.created_at.strftime("%Y-%m-%d %X") 
            dict["parent_name"] = snapshot.parent and snapshot.parent or "-1"
            return dict

    def _snapshot_create(self, instance, snapshot_name, snapshot_desc, parent_name):
        session = Session()
        try:
            result1 = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"]).update({'is_current': '0'}, synchronize_session=False)
            snapshot_record = Snapshot(name=snapshot_name, desc=snapshot_desc, parent=parent_name, instance_uuid=instance["uuid"], project_id=instance["project_id"], user_id=instance["user_id"],
                                       is_current='1')
            session.add(snapshot_record)
            session.commit()
        except Exception:
            pass
        finally:
            if session:
                session.close()

    def _snapshot_delete(self, instance, snapshot_name):
        session = Session()
        try:
            snapshot_record = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"], Snapshot.name == snapshot_name).first()
            if snapshot_record:
                snapshot_name = snapshot_record.name
                snapshot_parent = snapshot_record.parent
                snapshot_record.deleted = '1'
                snapshot_record.deleted_at = time.strftime("%Y-%m-%d %X", time.localtime())
                snapshot_record.updated_at = time.strftime("%Y-%m-%d %X", time.localtime())
                session.add(snapshot_record)
                # 重新生成父子关系
                result1 = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"], Snapshot.parent == snapshot_name).update({'parent': snapshot_parent},
                                                                                                                                                                     synchronize_session=False)
                session.commit()
        except Exception:
            pass
        finally:
            if session:
                session.close()

    def _snapshot_revert(self, instance, snapshot_name):
        session = Session()
        snapshot=Snapshot()
        try:
            snapshot_record = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"], Snapshot.name == snapshot_name).first()
            if snapshot_record:
                snapshot.name=snapshot_record.name
                snapshot.desc=snapshot_record.desc
                snapshot.parent=snapshot_record.parent
                snapshot.created_at=snapshot_record.created_at
                # 重置当前快照位置
                result1 = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"]).update({'is_current': '0'}, synchronize_session=False)
                session.commit()
                result2 = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"], Snapshot.name == snapshot_name).update({'is_current': '1'},                                                                                                                                      synchronize_session=False)
                session.commit()
                return snapshot
            return None
        except Exception:
            pass
        finally:
            if session:
                session.close()
    def _snapshot_current(self, instance):
        session = Session()
        try:
            snapshot_record = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"], Snapshot.is_current == '1').first()
            return snapshot_record
        except Exception:
            pass
        finally:
            if session:
                session.close()
    
    def _snapshot_exec(self, instance, snapshot_name, args):
        context = context.elevated()
        # 申请快照配额
        quotas = objects.Quotas(context)
        quotas.reserve(context, snapshots=1)
        try:
            LOG.audit(_('snapshot_create'), context=context, instance=instance)
            current_snapshot = self._current_snapshot(instance)
            parent_name =""
            if current_snapshot:
                parent_name = current_snapshot.name

            # 记录到数据库
            parent_name_str = self._unicode_to_str(parent_name)
            # 中文必须为str,不能是unicode
            snapshot_name_str = self._unicode_to_str(snapshot_name)
            snapshot_desc_str = self._unicode_to_str(snapshot_desc)
            instance_file = "%s/%s/disk" % (CONF.instances_path,instance["uuid"])
            try:
                virsh_cmd = ('qemu-img', 'snapshot', '-c', snapshot_name_str, instance_file)
                utils.execute(*virsh_cmd)
            except Exception:
                raise
            # 快照创建完成后写到数据库
            self._snapshot_create(instance, snapshot_name, snapshot_desc, parent_name_str)
            LOG.audit(_('snapshot_create success'), context=context, instance=instance)
            # 提交配额
            quotas.commit()
            return json.dumps(self._toDict(self._current_snapshot(instance)), ensure_ascii=False)
        except Exception:
            msg = 'snapshot_create is Failed,instance_id=%s,snapshot_name=%', (instance["uuid"], snapshot_name)
            LOG.exception(msg, instance=instance)
            quotas.rollback()
            raise

    def _has_snapshot(self, instance, snapshot_name):
        session = Session()
        try:
            snapshot_record = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"], Snapshot.name == snapshot_name).first()
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

    def _set_current_snapshot(self, instance, snapshot_name):
        # 设置当前快照
        session = Session()
        try:
            result1 = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"]).update({'is_current': '0'}, synchronize_session=False)
            result2 = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"], Snapshot.name == snapshot_name).update({'is_current': '1'},
                                                                                                                                                               synchronize_session=False)
            session.commit()
        except Exception:
            raise
        finally:
            if session:
                session.close()

    def _current_snapshot(self, instance):
        # 设置当前快照
        session = Session()
        try:
            result = session.query(Snapshot).filter(Snapshot.deleted == 0, Snapshot.instance_uuid == instance["uuid"], Snapshot.is_current == '1').first()
            return result
        except Exception:
            raise
        finally:
            if session:
                session.close()

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
