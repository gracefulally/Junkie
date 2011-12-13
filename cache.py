#!/usr/bin/python
# -*- coding=utf8 -*-

import sys
import StringIO
import os.path
import urlparse

import pickle

import magic

from twisted.internet import defer, reactor

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
  def __init__(self, key, path):
    self.path = path
    self.key = key
    self.readRequests = []
    self.datafile = None
    self.fname = None
    self.message = None

  def _make_path(self):
    return os.path.join(self.path, self.fname)

  def _read(self):
    return self.datafile.clone()

  def read(self):
    d = defer.Deferred()
    self.readRequests.append(d)
    if self.datafile:
      print >> sys.stderr, 'immediate read from memory for %s'%(self.key,)
      self.onReadyToRead()
    elif self.fname:
      print >> sys.stderr, 'immediate read from disk for %s'%(self.key,)
      self.readFromFile()
    else:
      print >> sys.stderr, 'waiting web for %s'%(self.key,)
    return d
    
  def abort(self):
    for d in self.readRequests:
      reactor.callLater(0, d.errback, None)
    self.readRequests = []

  def onReadyToRead(self):
    assert self.datafile
    for d in self.readRequests:
      reactor.callLater(0, d.callback, self._read()) 
    self.readRequests = []

  def write(self, datafile):
    assert not self.datafile
    self.datafile = datafile.clone()
    self.onReadyToRead()

  def writeToFile(self):
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
    
  def readFromFile(self):
    p = self._make_path()
    with open(p, 'r') as f:
      self.datafile = DataFile(f.read(), self.message, self.contentType)
    if self.datafile:
      self.onReadyToRead()
   

class Storage(object):
  '''
    Storage to hold cached contents
  '''
  def __init__(self, path):
    self.path = path
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
    entry = CacheEntry(key, self.path)
    self.index[key] = entry
    return entry

  def get(self, key):
    return self.index.get(key, None)

  def pop(self, key):
    return self.index.pop(key)
    
  def load_index(self):
    p = self._make_path('index.pickle')
    
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
    for entry in self.index.itervalues():
      if entry.datafile:
        entry.writeToFile()

  def save_index(self):
    p = self._make_path('index.pickle')
    for entry in self.index.itervalues():
      entry.abort()
    with open(p, 'w') as f:
      pickle.dump(self.index, f)

  def fix(self):
    to_delete = []
    for k, v in self.index.items():
      p = self._make_path(v)
      try:
        f = open(p)
        f.close()
      except:
        to_delete.append[k]
    for k in to_delete:
      del self.index[k]
    self.save_index()
