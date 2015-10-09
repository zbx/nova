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
    state = Column(Integer, default=0) #0:创建中,1:创建完成

    id = Column(Integer, primary_key=True)
    instance_uuid = Column(String(36))
    name = Column(String(32))
    desc = Column(String(32))
    parent = Column(String(32))


Base.metadata.create_all(engine)
