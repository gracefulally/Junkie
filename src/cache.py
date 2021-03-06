#!/usr/bin/python
# -*- coding=utf8 -*-

import exceptions
import sys
import StringIO
import os
import os.path
import urlparse
import pickle
import time

import magic
from ordereddict import OrderedDict

from twisted.python.failure import Failure
from twisted.internet import defer, reactor

class Cancel(exceptions.StandardError):
  pass

def url2name(url):
  parsed = urlparse.urlparse(url)
  return os.path.basename(parsed[2])#'path'


class DataFile(StringIO.StringIO):
  def __init__(self, data, message, mime=None, dontguess=None):
    StringIO.StringIO.__init__(self, data)
    self.message = message
    if mime:
      self.contentType = mime
    elif dontguess:
      self.contentType = 'application/octet-stream' #default
    else:
      self.contentType = magic.from_buffer(data, mime=True) 
  
  def clone(self):
    return DataFile(self.getvalue(), self.message, self.contentType)


class CacheEntry(object):
  def __init__(self, key, path, last_touch, notify):
    self.path = path
    self.key = key
    self.readRequests = []
    self.datafile = None
    self.fname = None
    self.message = None
    self.last_touch = last_touch
    self.notify = notify

  def _make_path(self):
    return os.path.join(self.path, self.fname)

  def _read(self):
    return self.datafile.clone()

  def status(self):
    if self.datafile and self.datafile.contentType:
      return 'on memory'
    elif self.fname:
      return 'on disk'
    else:
      return 'requesting'

  def touch(self):
    self.last_touch = time.time()
    assert self.last_touch > 1.0

  def read(self):
    d = defer.Deferred()
    self.readRequests.append(d)
    if self.datafile:
      print >> sys.stderr, 'immediate read from memory for %s'%(self.key,)
      self.onReadyToRead()
      h = self.notify
      h(self)
    elif self.fname:
      print >> sys.stderr, 'immediate read from disk for %s'%(self.key,)
      self.move_from_file()
    elif self.guess_fname_and_found():
      print >> sys.stderr, 'found orphan on disk for %s'%(self.key,)
      self.move_from_file()
    else:
      print >> sys.stderr, 'waiting web for %s'%(self.key,)
    return d
    
  def abort(self):
    print 'aborting ', self.key
    for d in self.readRequests:
      reactor.callLater(0, d.errback, Failure(Cancel()))
    self.readRequests = []

  def halt(self):
    self.notify = None

  def onReadyToRead(self):
    assert self.datafile
    for d in self.readRequests:
      reactor.callLater(0, d.callback, self._read()) 
    self.readRequests = []

  def write(self, datafile):
    assert not self.datafile
    self.datafile = datafile.clone()
    self.onReadyToRead()
    h = self.notify
    h(self)

  def move_to_disk(self):
    assert self.datafile
    if not self.fname:
      self.fname = url2name(self.key)
    p = self._make_path()
    print >>sys.stderr, 'trying %s, %s'%(self.fname, self.key,)
    with open(p, 'w') as f:
      f.write(self.datafile.read())
      self.message = self.datafile.message
      self.contentType = self.datafile.contentType
      self.datafile = None
      print >>sys.stderr, 'wrote %s, %s'%(self.fname, self.key,)
    
  def guess_fname_and_found(self):
    self.fname = url2name(self.key)
    guessed = self._make_path()
    if not os.access(guessed, os.F_OK):
      self.fname = None #undo
      return False
    
    return True
    #self.fname 


  def move_from_file(self):
    assert self.datafile is None
    assert self.fname
    p = self._make_path()
    with open(p, 'r') as f:
      self.datafile = DataFile(f.read(), self.message) #FIXME contentType
    if self.datafile:
      self.onReadyToRead()
      h = self.notify
      h(self)
    else:
      raise

  def serialize(self):
    assert self.datafile is None
   

class Cache(object):
  '''
    Cache to hold cached contents
  '''
  index_filename = "index.pickle"
  # active requests are not counted.

  def __init__(self, path, entry_limit):
    self.path = path
    self.on_memory_entry_limit = entry_limit
    self.on_memory = OrderedDict() #FastAVLTree()
    self.index = {}
    self.load_index()

  def __contains__(self, key):
    return key in self.index 

  def __len__(self):
    return len(self.index)
  
  def __iter__(self):
    return self.index.values()

  def _make_path(self, fname):
    return os.path.join(self.path, fname)

  def make_entry(self, key):
    '''
      reserve
    '''
    assert key not in self.index
    entry = CacheEntry(key, self.path, 0.0, self.on_notify)
    #FIXME because cache entry is write once. read many.
    self.index[key] = entry
    return entry

  def on_notify(self, entry):
    print 'cache entries: ', len(self.on_memory)
    if entry.key in self.on_memory:
      self.touch(entry)
    else:
      self.push_to_memory(entry)

  def push_to_memory(self, entry):
    if len(self.on_memory) >=  self.on_memory_entry_limit:
      key, purged = self.on_memory.popitem(False) #popping first item
      print 'purged cache life=%f s since %f for %s' % (time.time() - purged.last_touch, purged.last_touch, purged.key)
      purged.move_to_disk()
    entry.touch()
    print "putting entry %s" % (entry.key)
    assert entry.datafile
    assert entry.last_touch > 1.0
    self.on_memory[entry.key] = entry

  def touch(self, entry):
    #revoke
    x = self.on_memory.pop(entry.key)
    assert x == entry
    # activate it as a new entry
    entry.touch()
    self.on_memory[entry.key] = entry

  def get(self, key):
    e = self.index.get(key, None)
    return e

  def pop(self, key):
    return self.index.pop(key)
    
  def load_index(self):
    p = self._make_path(self.index_filename)
    
    no_index = False
    try:
      f = open(p)
    except:
      f = None
      pass
    if f:
      try:
        self.index = pickle.load(f)
      except:
        no_index = True        
      finally:
        f.close()
    else:
      no_index = True

    if no_index: 
      self.index = {}
      self.save_index()

  def save_entries(self):
    print 'Cache.save_entries'
    for entry in self.index.itervalues():
      if entry.datafile:
        entry.move_to_disk()

  def save_index(self):
    print 'Cache.save_index'
    p = self._make_path(self.index_filename)
    for entry in self.index.itervalues():
      entry.abort()
    with open(p, 'w') as f:
      pickle.dump(self.index, f)

  def fix(self):
    to_delete = []
    for k, v in self.index.items():
      p = self._make_path(v)
      if not os.access(p, os.F_OK | os.R_OK | os.W_OK):
        to_delete.append[k]
    for k in to_delete:
      del self.index[k]
    self.save_index()

  def scan(self):
    ''' wrong idea. cant generate url from file name...'''
    #return os.path.join(self.path, fname)
    for fname in os.listdir(self.path):
      if fname == self.index_filename:
        continue

    

  def html_index(self):
    count = len(self.index)
    x = []
    for key, ce in self.on_memory.iteritems():
      x.append('<li>[%s ]: %s</li>\n'%(key, ce.status()))
    frag_mem = '<ol>%s</ol>'%(''.join(x))

    y = []
    for key, ce in self.index.iteritems():
      y.append('<li>[%s ]: %s</li>\n'%(key, ce.status()))
    frag_index = '<ol>%s</ol>'%(''.join(y))
    html = '''<html><body>
      <p>count:%s</p>
      %s
      <hr />
      %s
      </body></html>'''
    return html%(count, frag_mem, frag_index)

    



