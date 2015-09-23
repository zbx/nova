from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from oslo.config import cfg
from nova.openstack.common import log as logging


baxia_sql_connection = [
    cfg.StrOpt('baxia_sql_connection'),
]

CONF = cfg.CONF
CONF.register_opts(baxia_sql_connection)


engine = create_engine(CONF.baxia_sql_connection, echo=False)

Base = declarative_base()
Session = sessionmaker(bind=engine)

class BaseMixin(object):
    def to_dict(self):
        return {key: getattr(self, key) for key in [prop.key for prop in self.__mapper__.iterate_properties]}

    def __repr__(self):
        return '<%s(%s)>' % (type(self).__name__, ', '.join(map(lambda x: '%s=%s' % (x[0], repr(x[1])), self.to_dict().items())))

