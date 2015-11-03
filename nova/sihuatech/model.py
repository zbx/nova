# -*- coding: utf-8 -*-
from sqlalchemy import Column, Integer, String, DateTime, Boolean
import time
from nova.sihuatech.orm import *
from nova.openstack.common import timeutils


class Snapshot(Base,BaseMixin):
    __tablename__ = 'snapshots'

    created_at = Column(DateTime, default=lambda: time.strftime("%Y-%m-%d %X", time.localtime()))
    updated_at = Column(DateTime)
    deleted_at = Column(DateTime)
    deleted = Column(Integer, default=0) #0:未删除,1:已删除

    id = Column(Integer, primary_key=True)
    instance_uuid = Column(String(36))
    project_id = Column(String(36))
    user_id = Column(String(36))
    name = Column(String(32))
    desc = Column(String(32))
    parent = Column(String(32))
    is_current = Column(Integer, default=0)#0:非当前,1:当前快照
    xml = Column(String(8192))


Base.metadata.create_all(engine)
