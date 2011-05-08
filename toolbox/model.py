"""
models for toolbox
"""

import couchdb
import os
import sys
from copy import deepcopy
from search import WhooshSearch
from time import time
from util import str2filename

try:
    import json
except ImportError:
    import simplejson as json

# TODO: types of fields:
# - string: a single string: {'type': 'string', 'name': 'name', 'required': True}
# - field: a list of strings: {'type': 'field', 'name', 'usage'}
# - dict: a subclassifier: {'type': '???', 'name': 'url', 'required': True}
# - computed values, such as modified

class ProjectsModel(object):
    """
    abstract base class for toolbox tools
    """

    def __init__(self, fields=None, required=('name', 'description', 'url'),
                 whoosh_index=None):
        """
        - fields : list of fields to use, or None to calculate dynamically
        - required : required data (strings)
        - whoosh_index : directory to keep whoosh index in
        """
        self.required = set(required)

        # reserved fields        
        self.reserved = self.required.copy()
        self.reserved.update(['modified']) # last modified, a computed value
        self.search = WhooshSearch(whoosh_index=whoosh_index)

        # classifier fields
        self._fields = fields
        self.field_set = set(fields or ())

    def update_search(self, project):
        """update the search index"""
        assert self.required.issubset(project.keys()) # XXX should go elsewhere
        fields = dict([(field, project[field])
                       for field in self.fields()
                       if field in project])

        # keys must be strings, not unicode, on some systems
        f = dict([(str(i), j) for i, j in fields.items()])

        self.search.update(name=project['name'], description=project['description'], **f)

    def fields(self):
        """what fields does the model support?"""
        if self._fields is not None:
            return self._fields
        return list(self.field_set)

    def projects(self):
        """list of all projects"""
        return [i['name'] for i in self.get()]

    def export(self, other):
        """export the current model to another model instance"""
        for project in self.get():
            other.update(project)

    def rename_field_value(self, field, from_value, to_value):
        projects = self.get(None, **{field: from_value})
        for project in projects:
            project[field].remove(from_value)
            project[field].append(to_value)
            self.update(project)

    ### implementor methods

    def update(self, project):
        """update a project"""
        raise NotImplementedError

    def get(self, search=None, **query):
        """
        get a list of projects matching a query
        the query should be key, value pairs to match;
        if the value is single, it should be a string;
        if the value is multiple, it should be a set which will be
        ANDed together
        """
        raise NotImplementedError

    def project(self, name):
        """get a project of a particular name, or None if there is none"""
        raise NotImplementedError

    def field_query(self, field):
        """get projects according to a particular field, or None"""
        raise NotImplementedError

    def delete(self, project):
        raise NotImplementedError


class MemoryCache(ProjectsModel):
    """
    sample implementation keeping everything in memory
    """

    def __init__(self, fields=None, whoosh_index=None):
        
        ProjectsModel.__init__(self, fields=fields, whoosh_index=whoosh_index)

        # indices
        self._projects = {}
        self.index = {}
        
        self.load()

    def update(self, project, load=False):
        
        if project['name'] in self._projects and project == self._projects[project['name']]:
            return # nothing to do
        if not load:
            project['modified'] = time()
        if self._fields is None:
            fields = [i for i in project if i not in self.reserved]
            self.field_set.update(fields)
        else:
            fields = self._fields
        for field in fields:
            for key, _set in self.index.get(field, {}).items():
                _set.discard(project['name'])
                if not _set:
                    self.index[field].pop(key)
            if field not in project:
                continue
            project[field] = list(set(project[field]))
            index = self.index.setdefault(field, {})
            values = project[field]
            if isinstance(values, basestring):
                values = [values]
            for value in values:
                index.setdefault(value, set()).update([project['name']])
        self._projects[project['name']] = deepcopy(project)
        self.update_search(project)
        if not load:
            self.save(project)

    def get(self, search=None, **query):
        """
        - search: text search
        - query: fields to match
        """
        order = None
        if search:
            results = self.search(search)
            order = dict([(j,i) for i,j in enumerate(results)])
        else:
            results = self._projects.keys()
        results = set(results)
        for key, value in query.items():
            results.intersection_update(self.index.get(key, {}).get(value, set()))
        if order:
            # preserve search order
            results = sorted(list(results), key=lambda x: order[x])
        return [deepcopy(self._projects[project]) for project in results]


    def project(self, name):
        if name in self._projects:
            return deepcopy(self._projects[name])

    def field_query(self, field):
        return self.index.get(field)

    def delete(self, project):
        """
        delete a project
        - project : name of the project
        """
        if project not in self._projects:
            return
        del self._projects[project]
        for key, value in self.index.items():
            if project in value:
                if len(value) == 1:
                    self._fields.pop(key)
                value.pop(project)
        self.search.delete(project)
        
    def load(self):
        """for subclasses; in memory, load nothing"""

    def save(self, project):
        """for subclasses; in memory, save nothing"""


class FileCache(MemoryCache):
    """save in JSON blob directory"""

    def __init__(self, directory, fields=None, whoosh_index=None):
        """
        - directory: directory of .json tool files
        """
        # JSON blob directory
        if not os.path.exists(directory):
            os.makedirs(directory)
        assert os.path.isdir(directory)
        self.directory = directory

        self.files = {}
        MemoryCache.__init__(self, fields=fields, whoosh_index=whoosh_index)

    def delete(self, project):
        MemoryCache.delete(self, project)
        os.remove(os.path.join(self.directory, self.files.pop(project)))

    def load(self):
        """load JSON from the directory"""
        for i in os.listdir(self.directory):
            if not i.endswith('.json'):
                continue
            filename = os.path.join(self.directory, i)
            try:
                project = json.loads(file(filename).read())
            except:
                print 'File: ' + i
                raise
            self.files[project['name']] = i
            self.update(project, load='modified' in project)

    def save(self, project):

        filename = self.files.get(project['name'])
        if not filename:
            filename = str2filename(project['name']) + '.json'
        filename = os.path.join(self.directory, filename)
        file(filename, 'w').write(json.dumps(project))


class CouchCache(MemoryCache):
    """
    store json files in couchdb
    """

    def __init__(self,
                 server="http://127.0.0.1:5984",
                 dbname="toolbox",
                 fields=None,
                 whoosh_index=None):

        # TODO: check if server is running
        couchserver = couchdb.Server(server)
        try:
            self.db = couchserver[dbname]
        except couchdb.ResourceNotFound: # XXX should not be a blanket except!
            self.db = couchserver.create(dbname)
        except:
            raise Exception("Could not connect to couch instance. Make sure that you have couch running at %s and that you have database create priveleges if '%s' does not exist" % (server, dbname))
        MemoryCache.__init__(self, fields=fields, whoosh_index=whoosh_index)

    def load(self):
        """load JSON objects from CouchDB docs"""
        for id in self.db:
            doc = self.db[id]
            project = doc['project']
            self.update(project, load=True)
            
    def save(self, project):
        name = project['name']
        try:
             updated = self.db[name]
        except:
             updated = {}
        updated['project'] = project
        self.db[name] = updated

    def delete(self, project):
        MemoryCache.delete(self, project)
        del self.db[project]

# directory of available models
models = {'memory_cache': MemoryCache,
          'file_cache': FileCache,
          'couch': CouchCache}

def convert(args=sys.argv[1:]):
    """CLI front-end for model conversion"""
    from optparse import OptionParser
    usage = '%prog [global-options] from_model [options] to_model [options]'
    description = "export data from one model to another"
    parser = OptionParser(usage=usage, description=description)
    parser.disable_interspersed_args()
    parser.add_option('-l', '--list-models', dest='list_models',
                      action='store_true', default=False,
                      help="list available models")
    parser.add_option('-a', '--list-args', dest='list_args',
                      metavar='MODEL',
                      help="list arguments for a model")

    options, args = parser.parse_args(args)

    # process global options
    if options.list_models:
        for name in sorted(models.keys()):
            print name # could conceivably print docstring
        parser.exit()
    if options.list_args:
        if not options.list_args in models:
            parser.error("Model '%s' not found. (Choose from: %s)" % (options.list_args, models.keys()))
        ctor = models[options.list_args].__init__
        import inspect
        argspec = inspect.getargspec(ctor)
        defaults = [[i, None] for i in argspec.args[1:]] # ignore self
        for index, value in enumerate(reversed(argspec.defaults), 1):
            defaults[-index][-1] = value
        defaults = [[i,j] for i, j in defaults if i != 'fields']
        print '%s arguments:' % options.list_args
        for arg, value in defaults:
            print ' -%s %s' % (arg, value or '')
        parser.exit()

    # parse models and their ctor args
    sects = []
    _models = []
    for arg in args:
        if arg.startswith('-'):
            sects[-1].append(arg)
        else:
            _models.append(arg)
            sects.append([])

    # check models
    if len(_models) != 2:
        parser.error("Please provide two models. (You gave: %s)" % _models)
    if not set(_models).issubset(models):
        parser.error("Please use these models: %s (You gave: %s)" % (models, _models))

    sects = [ [i.lstrip('-') for i in sect ] for sect in sects ]

    # require an equals sign
    # XXX hacky but much easier to parse
    if [ True for sect in sects
         if [i for i in sect if '=' not in i] ]:
        parser.error("All arguments must be `key=value`")
    sects = [dict([i.split('=', 1) for i in sect]) for sect in sects]

    # instantiate models
    from_model = models[_models[0]](**sects[0])
    to_model = models[_models[1]](**sects[1])

    # convert the data
    from_model.export(to_model)

if __name__ == '__main__':
    convert()
