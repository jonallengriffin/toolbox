"""
request dispatcher WSGI app:
data persisting across requests should go here
"""

import os

from handlers import CreateProjectView
from handlers import DeleteProjectHandler
from handlers import FieldView
from handlers import ProjectView
from handlers import QueryView
from handlers import TagsView
from handlers import AboutView

from model import models
from webob import Request, Response, exc

here = os.path.dirname(os.path.abspath(__file__))

class Dispatcher(object):
    """toolbox WSGI app which dispatchers to associated handlers"""

    # class defaults
    defaults = { 'about': None, # file path to ReST about page
                 'model_type': 'memory_cache', # type of model to use
                 'handlers': None,
                 'reserved': None, # reserved URL namespaces
                 'template_dir': None, # directory for template overrides
                 }

    def __init__(self, **kw):
        """
        **kw arguments used to override defaults
        additional **kw are passed to the model
        """

        # set instance parameters from kw and defaults
        for key in self.defaults:
            setattr(self, key, kw.pop(key, self.defaults[key]))

        # model: backend storage and associated methods
        if 'fields' in kw and isinstance(kw['fields'], basestring):
            # split fields if given as a string
            kw['fields'] = kw['fields'].split()
        if hasattr(self.model_type, '__call__'):
            model = self.model_type
        elif self.model_type in models:
            model = models[self.model_type]
        else:
            try:
                import pyloader
                model = pyloader.load(self.model_type)
            except:
                raise AssertionError("model_type '%s' not found in %s" % (self.model_type, models.keys()))
        self.model = model(**kw)

        # add an about view if file specified
        if self.about:
            about = file(self.about).read()
            import docutils.core
            about = docutils.core.publish_parts(about, writer_name='html')['body']
            self.about = about


        # request handlers in order they will be tried
        if self.handlers is None:
            self.handlers = [ TagsView,
                              CreateProjectView,
                              FieldView,
                              ProjectView,
                              QueryView,
                              DeleteProjectHandler ]
            if self.about:
                self.handlers.append(AboutView)

        # extend reserved URLS from handlers
        if self.reserved is None:
            self.reserved = set(['css', 'js', 'img'])
            for handler in self.handlers:
                if handler.handler_path:
                    self.reserved.add(handler.handler_path[0])

    def __call__(self, environ, start_response):

        # get a request object
        request = Request(environ)

        # get the path 
        path = request.path_info.strip('/').split('/')
        if path == ['']:
            path = []
        request.environ['path'] = path

        # load any new data
        self.model.load()

        # match the request to a handler
        for h in self.handlers:
            handler = h.match(self, request)
            if handler is not None:
                break
        else:
            # TODO: our own 404 handler with a menu
            handler = exc.HTTPNotFound

        # get response
        res = handler()
        return res(environ, start_response)
