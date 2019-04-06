#!/usr/bin/env python
# -*- coding: utf-8 -*-

###############################################################################
#  Copyright 2013 Kitware Inc.
#
#  Licensed under the Apache License, Version 2.0 ( the "License" );
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

import six

from ..describe import Description, autoDescribeRoute
from ..rest import Resource as BaseResource, setResponseHeader, setContentDisposition
from girder.constants import AccessType, TokenScope
from girder.exceptions import RestException
from girder.api import access
from girder.utility import parseTimestamp
from girder.utility.search import getSearchModeHandler
from girder.utility import ziputil
from girder.utility import path as path_util
from girder.utility.progress import ProgressContext

# Plugins can modify this set to allow other types to be searched
allowedSearchTypes = {'collection', 'file', 'folder', 'group', 'item', 'user'}
allowedDeleteTypes = {'collection', 'file', 'folder', 'group', 'item', 'user'}


class Resource(BaseResource):
    """
    API Endpoints that deal with operations across multiple resource types.
    """
    def __init__(self):
        super(Resource, self).__init__()
        print('(#####)girder/girder/api/vi/rescource.py-__init__()')
        self.resourceName = 'resource'
        self.route('GET', ('search',), self.search)
        self.route('GET', ('lookup',), self.lookup)
        self.route('GET', (':id',), self.getResource)
        self.route('GET', (':id', 'path'), self.path)
        self.route('PUT', (':id', 'timestamp'), self.setTimestamp)
        self.route('GET', ('download',), self.download)
        self.route('POST', ('download',), self.download)
        self.route('PUT', ('move',), self.moveResources)
        self.route('POST', ('copy',), self.copyResources)
        self.route('DELETE', (), self.delete)
        self.route('POST',  ('slide',), self.excuteAI)

    @access.public
    @autoDescribeRoute(
        Description('Search for resources in the system.')
        .param('q', 'The search query.')
        .param('mode', 'The search mode. Can always use either a text search or a '
               'prefix-based search.', required=False, default='text')
        .jsonParam('types', 'A JSON list of resource types to search for, e.g. '
                   '["user", "folder", "item"].', requireArray=True)
        .param('level', 'Minimum required access level.', required=False,
               dataType='integer', default=AccessType.READ)
        .pagingParams(defaultSort=None, defaultLimit=10)
        .errorResponse('Invalid type list format.')
    )
    def search(self, q, mode, types, level, limit, offset):
        """
        Perform a search using one of the registered search modes.
        """
        level = AccessType.validate(level)
        user = self.getCurrentUser()
        handler = getSearchModeHandler(mode)
        if handler is None:
            raise RestException('Search mode handler %r not found.' % mode)
        results = handler(
            query=q,
            types=types,
            user=user,
            limit=limit,
            offset=offset,
            level=level
        )
        return results

    def _validateResourceSet(self, resources, allowedModels=None):
        """
        Validate a set of resources against a set of allowed models.
        Also ensures the requested resource set is not empty.
        # TODO jsonschema could replace this probably

        :param resources: The set of resources requested.
        :param allowedModels: if present, an iterable of models that may be
            included in the resources.
        """
        if allowedModels:
            invalid = set(resources.keys()) - set(allowedModels)
            if invalid:
                raise RestException('Invalid resource types requested: ' + ', '.join(invalid))
        count = sum([len(v) for v in six.viewvalues(resources)])
        if not count:
            raise RestException('No resources specified.')

    def _getResourceModel(self, kind, funcName=None):
        """
        Load and return a model with a specific function or throw an exception.

        :param kind: the name of the model to load
        :param funcName: a function name to ensure that each model contains.
        :returns: the loaded model.
        """
        try:
            model = self.model(kind)
        except ImportError:
            model = None
        if not model or (funcName and not hasattr(model, funcName)):
            raise RestException('Invalid resources format.')
        return model

    @access.public(scope=TokenScope.DATA_READ)
    @autoDescribeRoute(
        Description('Look up a resource in the data hierarchy by path.')
        .param('path',
               'The path of the resource.  The path must be an absolute Unix '
               'path starting with either "/user/[user name]", for a user\'s '
               'resources or "/collection/[collection name]", for resources '
               'under a collection.')
        .param('test',
               'Specify whether to return None instead of throwing an '
               'exception when path doesn\'t exist.',
               required=False, dataType='boolean', default=False)
        .errorResponse('Path is invalid.')
        .errorResponse('Path refers to a resource that does not exist.')
        .errorResponse('Read access was denied for the resource.', 403)
    )
    def lookup(self, path, test):
        return path_util.lookUpPath(path, self.getCurrentUser(), test)['document']

    @access.public(scope=TokenScope.DATA_READ)
    @autoDescribeRoute(
        Description('Get path of a resource.')
        .param('id', 'The ID of the resource.', paramType='path')
        .param('type', 'The type of the resource (item, file, etc.).')
        .errorResponse('ID was invalid.')
        .errorResponse('Invalid resource type.')
        .errorResponse('Read access was denied for the resource.', 403)
    )
    def path(self, id, type):
        user = self.getCurrentUser()
        doc = self._getResource(id, type)
        if doc is None:
            raise RestException('Invalid resource id.')
        return path_util.getResourcePath(type, doc, user=user)

    @access.cookie(force=True)
    @access.public(scope=TokenScope.DATA_READ)
    @autoDescribeRoute(
        Description('Download a set of items, folders, collections, and users '
                    'as a zip archive.')
        .notes('This route is also exposed via the POST method because the '
               'request parameters can be quite long, and encoding them in the '
               'URL (as is standard when using the GET method) can cause the '
               'URL to become too long, which causes errors.')
        .jsonParam('resources', 'A JSON-encoded set of resources to download. Each type is '
                   'a list of ids. For example: {"item": [(item id 1), (item id 2)], '
                   '"folder": [(folder id 1)]}.', requireObject=True)
        .param('includeMetadata', 'Include any metadata in JSON files in the '
               'archive.', required=False, dataType='boolean', default=False)
        .produces('application/zip')
        .errorResponse('Unsupported or unknown resource type.')
        .errorResponse('Invalid resources format.')
        .errorResponse('No resources specified.')
        .errorResponse('Resource not found.')
        .errorResponse('Read access was denied for a resource.', 403)
    )
    def download(self, resources, includeMetadata):
        """
        Returns a generator function that will be used to stream out a zip
        file containing the listed resource's contents, filtered by
        permissions.
        """
        print('(#####) girder/girder/api/v1/resource.py-download')
        user = self.getCurrentUser()
        self._validateResourceSet(resources)
        # Check that all the resources are valid, so we don't download the zip
        # file if it would throw an error.
        for kind in resources:
            model = self._getResourceModel(kind, 'fileList')
            for id in resources[kind]:
                if not model.load(id=id, user=user, level=AccessType.READ):
                    raise RestException('Resource %s %s not found.' % (kind, id))
        setResponseHeader('Content-Type', 'application/zip')
        setContentDisposition('Resources.zip')

        def stream():
            zip = ziputil.ZipGenerator()
            for kind in resources:
                model = self.model(kind)
                for id in resources[kind]:
                    doc = model.load(id=id, user=user, level=AccessType.READ)
                    for (path, file) in model.fileList(
                            doc=doc, user=user, includeMetadata=includeMetadata, subpath=True):
                        for data in zip.addFile(file, path):
                            yield data
            yield zip.footer()
        return stream

    @access.user(scope=TokenScope.DATA_OWN)
    @autoDescribeRoute(
        Description('Delete a set of items, folders, or other resources.')
        .jsonParam('resources', 'A JSON-encoded set of resources to delete. Each '
                   'type is a list of ids.  For example: {"item": [(item id 1), '
                   '(item id2)], "folder": [(folder id 1)]}.', requireObject=True)
        .param('progress', 'Whether to record progress on this task.',
               default=False, required=False, dataType='boolean')
        .errorResponse('Unsupported or unknown resource type.')
        .errorResponse('Invalid resources format.')
        .errorResponse('No resources specified.')
        .errorResponse('Resource not found.')
        .errorResponse('Admin access was denied for a resource.', 403)
    )
    def delete(self, resources, progress):
        user = self.getCurrentUser()
        self._validateResourceSet(resources, allowedDeleteTypes)
        total = sum([len(resources[key]) for key in resources])
        with ProgressContext(
                progress, user=user, title='删除对象.',
                message='Calculating size...') as ctx:
            ctx.update(total=total)
            current = 0
            for kind in resources:
                model = self._getResourceModel(kind, 'remove')
                for id in resources[kind]:
                    doc = model.load(id=id, user=user, level=AccessType.ADMIN, exc=True)

                    # Don't do a subtree count if we weren't asked for progress
                    if progress:
                        subtotal = model.subtreeCount(doc)
                        if subtotal != 1:
                            total += subtotal - 1
                            ctx.update(total=total)
                    model.remove(doc, progress=ctx)
                    if progress:
                        current += subtotal
                        if ctx.progress['data']['current'] != current:
                            ctx.update(current=current, message='Deleted ' + kind)

    def _getResource(self, id, type):
        model = self._getResourceModel(type)
        return model.load(id=id, user=self.getCurrentUser(), level=AccessType.READ)

    @access.admin
    @autoDescribeRoute(
        Description('Get any resource by ID.')
        .param('id', 'The ID of the resource.', paramType='path')
        .param('type', 'The type of the resource (item, file, etc.).')
        .errorResponse('ID was invalid.')
        .errorResponse('Read access was denied for the resource.', 403)
    )
    def getResource(self, id, type):
        return self._getResource(id, type)

    @access.admin
    @autoDescribeRoute(
        Description('Set the created or updated timestamp for a resource.')
        .param('id', 'The ID of the resource.', paramType='path')
        .param('type', 'The type of the resource (item, file, etc.).')
        .param('created', 'The new created timestamp.', required=False)
        .param('updated', 'The new updated timestamp.', required=False)
        .errorResponse('ID was invalid.')
        .errorResponse('Access was denied for the resource.', 403)
    )
    def setTimestamp(self, id, type, created, updated):
        user = self.getCurrentUser()
        model = self._getResourceModel(type)
        doc = model.load(id=id, user=user, level=AccessType.WRITE, exc=True)

        if created is not None:
            if 'created' not in doc:
                raise RestException('Resource has no "created" field.')
            doc['created'] = parseTimestamp(created)
        if updated is not None:
            if 'updated' not in doc:
                raise RestException('Resource has no "updated" field.')
            doc['updated'] = parseTimestamp(updated)
        return model.filter(model.save(doc), user=user)

    def _prepareMoveOrCopy(self, resources, parentType, parentId):
        user = self.getCurrentUser()
        self._validateResourceSet(resources, ('folder', 'item'))

        if resources.get('item') and parentType != 'folder':
            raise RestException('Invalid parentType.')
        return self.model(parentType).load(parentId, level=AccessType.WRITE, user=user, exc=True)

    @access.user(scope=TokenScope.DATA_WRITE)
    @autoDescribeRoute(
        Description('Move a set of items and folders.')
        .jsonParam('resources', 'A JSON-encoded set of resources to move. Each type '
                   'is a list of ids.  Only folders and items may be specified.  '
                   'For example: {"item": [(item id 1), (item id2)], "folder": '
                   '[(folder id 1)]}.', requireObject=True)
        .param('parentType', 'Parent type for the new parent of these resources.',
               enum=('user', 'collection', 'folder'))
        .param('parentId', 'Parent ID for the new parent of these resources.')
        .param('progress', 'Whether to record progress on this task.',
               required=False, default=False, dataType='boolean')
        .errorResponse('Unsupported or unknown resource type.')
        .errorResponse('Invalid resources format.')
        .errorResponse('Resource type not supported.')
        .errorResponse('No resources specified.')
        .errorResponse('Resource not found.')
        .errorResponse('ID was invalid.')
    )
    def moveResources(self, resources, parentType, parentId, progress):
        print('(#####) girder/girder/api/v1/resource.py-moveResources')
        user = self.getCurrentUser()
        parent = self._prepareMoveOrCopy(resources, parentType, parentId)
        total = sum([len(resources[key]) for key in resources])
        with ProgressContext(
                progress, user=user, title='移动对象',
                message='Calculating requirements...', total=total) as ctx:
            for kind in resources:
                model = self._getResourceModel(kind, 'move')
                for id in resources[kind]:
                    doc = model.load(id=id, user=user, level=AccessType.WRITE, exc=True)
                    ctx.update(message='Moving %s %s' % (kind, doc.get('name', '')))
                    if kind == 'item':
                        if parent['_id'] != doc['folderId']:
                            model.move(doc, parent)
                    elif kind == 'folder':
                        if ((parentType, parent['_id']) !=
                                (doc['parentCollection'], doc['parentId'])):
                            model.move(doc, parent, parentType)
                    ctx.update(increment=1)

    @access.user(scope=TokenScope.DATA_WRITE)
    @autoDescribeRoute(
        Description('Copy a set of items and folders.')
        .jsonParam('resources', 'A JSON-encoded set of resources to copy. Each type '
                   'is a list of ids.  Only folders and items may be specified.  '
                   'For example: {"item": [(item id 1), (item id2)], "folder": '
                   '[(folder id 1)]}.', requireObject=True)
        .param('parentType', 'Parent type for the new parent of these '
               'resources.')
        .param('parentId', 'Parent ID for the new parent of these resources.')
        .param('progress', 'Whether to record progress on this task.',
               required=False, default=False, dataType='boolean')
        .errorResponse('Unsupported or unknown resource type.')
        .errorResponse('Invalid resources format.')
        .errorResponse('Resource type not supported.')
        .errorResponse('No resources specified.')
        .errorResponse('Resource not found.')
        .errorResponse('ID was invalid.')
    )
    def copyResources(self, resources, parentType, parentId, progress):
        print('(#####) girder/girder/api/v1/resource.py-copyResources')
        user = self.getCurrentUser()
        parent = self._prepareMoveOrCopy(resources, parentType, parentId)
        total = len(resources.get('item', []))
        if 'folder' in resources:
            model = self._getResourceModel('folder')
            for id in resources['folder']:
                folder = model.load(id=id, user=user, level=AccessType.READ, exc=True)
                total += model.subtreeCount(folder)
        with ProgressContext(
                progress, user=user, title='拷贝对象',
                message='Calculating requirements...', total=total) as ctx:
            for kind in resources:
                model = self._getResourceModel(kind)
                for id in resources[kind]:
                    doc = model.load(id=id, user=user, level=AccessType.READ, exc=True)
                    ctx.update(message='Copying %s %s' % (kind, doc.get('name', '')))
                    if kind == 'item':
                        model.copyItem(doc, folder=parent, creator=user)
                        ctx.update(increment=1)
                    elif kind == 'folder':
                        model.copyFolder(
                            doc, parent=parent, parentType=parentType, creator=user, progress=ctx)

    @access.user(scope=TokenScope.DATA_OWN)
    @autoDescribeRoute(
        Description('Delete a set of items, folders, or other resources.')
        .jsonParam('resources', 'A JSON-encoded set of resources to delete. Each '
                   'type is a list of ids.  For example: {"item": [(item id 1), '
                   '(item id2)], "folder": [(folder id 1)]}.', requireObject=True)
        .param('progress', 'Whether to record progress on this task.',
               default=False, required=False, dataType='boolean')
        .errorResponse('Unsupported or unknown resource type.')
        .errorResponse('Invalid resources format.')
        .errorResponse('No resources specified.')
        .errorResponse('Resource not found.')
        .errorResponse('Admin access was denied for a resource.', 403)
    )
    
    #####################################################
    ################# PV AI diagnosis START #############
    #####################################################
    def excuteAI(self, resources, progress):
        import pymongo
        import ai_pathology as ai_pathology
        from bson.objectid import ObjectId
        import xml.dom.minidom
        user = self.getCurrentUser()
        print('(#####) girder/girder/api/v1/resource.py-excuteAI:user='+ str(user))
        print('(#####) girder/girder/api/v1/resource.py-excuteAI:user[_id]='+ str(user["_id"]))
        mongoClient = pymongo.MongoClient('mongodb://localhost:27017/')
        db_girder = mongoClient['girder']
        coll_file = db_girder["file"]
        total = sum([len(resources[key]) for key in resources])
        with ProgressContext(progress, user=user, 
                             title='AI病理诊断中...', 
                             message='AI病理诊断中...') as ctx:
            ctx.update(total=total) # total is the granularity of the progress
        for kind in resources:
            for id in resources[kind]:
                doc_file = coll_file.find_one({"itemId":ObjectId(id)})
                filePath= "/opt/histomicstk/".join(str(doc_file["path"]))
                anoFile = ai_pathology.ai_excuataion(filePath)
                svsFileName = doc_file["name"]
                print('(#####) girder/girder/api/v1/resource.py-excuteAI:id='+ (id))
                print('(#####) girder/girder/api/v1/resource.py-excuteAI:doc_file='+ str(doc_file))
                print('(#####) girder/girder/api/v1/resource.py-excuteAI:anoFile='+ str(anoFile))
                print('(#####) girder/girder/api/v1/resource.py-excuteAI:svsFileName='+ str(svsFileName))
                
                #####################################################
                ################# PV AI diagnosis START #############
                #####################################################
                coll_annotation = db_girder["annotation"]
            
                annotation_id = ObjectId()
                annotation={
                            "_id" : annotation_id,
                            ### "itemId" need to be update
                            "itemId" : ObjectId(id),
                            #################################################
                            # "updated" : ISODate("2018-12-13T08:59:09.307Z"),
                            "groups" : [],
                            # "created" : ISODate("2018-12-13T08:56:23.083Z"),
                            "_version" : 1,
                            "annotation" : {
                                "name" : "AI诊断结果"
                            },
                            "access" : {
                                "users" : [
                                    {
                                        "flags" : [],
                                        "id" : user["_id"],
                                        "level" : 2
                                    }
                                ],
                                "groups" : []
                            },
                            "creatorId" : user["_id"],
                            "public" : False,
                            "updatedId" : user["_id"]
                        }
                result = coll_annotation.insert_one(annotation)
            
                #get elements
                dom = xml.dom.minidom.parse("/home/ken/Documents/kfbSlides/old/AA01D201800252.kfb.Ano")
                root = dom.documentElement
            
                #####################################################
                ################ Get  pvAno Elements ################
                #####################################################
                annotations = root.getElementsByTagName('Annotations')[0]
                # print('1' + str(annotations))
                regions = annotations.getElementsByTagName('Regions')[0]
                # print('2' + str(regions))
                region = regions.getElementsByTagName('Region')
                # print('3' + str(region))
            
                for node_r in region:
                    print('3' + str(node_r.getAttribute('Detail')))
                    # print('4' + node.getAttribute('FigureType'))
                    vertices = node_r.getElementsByTagName('Vertices')[0]
                    vertice = node_r.getElementsByTagName('Vertice')
                    # print('5' + str(vertice))
                    points = []
                    for node_v in vertice:
                        X = node_v.getAttribute('X')
                        Y = node_v.getAttribute('Y')
                        # print('6: ' + X)
                        point = [float(X)*40, float(Y)*40, 0]
                        # print('7: ' + str(point))
                        points.append(point)
            
                    #####################################################
                    ############## Insert Annotationelement #############
                    #####################################################
                    coll_annotationelement = db_girder["annotationelement"]
                    annotationelement= {
                                        "_id" : ObjectId(),
                                        # "created" : ISODate("2018-12-13T08:59:09.339Z"),
                                        "annotationId" : annotation_id,
                                        "_version" : 1,
                                        "element" : {
                                            "closed" : False,
                                            "points" : points,
                                            "fillColor" : "rgba(0,0,0,0)",
                                            "lineColor" : "rgb(0,0,0)",
                                            "lineWidth" : 2,
                                            "type" : "polyline",
                                            "id" : str(ObjectId())
                                        },
                                        # "bbox" : {
                                        #     "highy" : 62041.6302682467,
                                        #     "highx" : 35963.8099634868,
                                        #     "highz" : 0,
                                        #     "details" : 60,
                                        #     "size" : 47311.8713726143,
                                        #     "lowz" : 0,
                                        #     "lowx" : 4516.38527529891,
                                        #     "lowy" : 26693.7498047797
                                        # }
                                    }
                    result = coll_annotationelement.insert_one(annotationelement)
        
        ctx.update(increment=1) # see notification model for other options
        
#         user = self.getCurrentUser()
#         self._validateResourceSet(resources, allowedDeleteTypes)
#         total = sum([len(resources[key]) for key in resources])
#         with ProgressContext(
#                 progress, user=user, title='AI诊断.',
#                 message='Calculating size...') as ctx:
#             ctx.update(total=total)
#             current = 0
#             for kind in resources:
#                 model = self._getResourceModel(kind, 'remove')
#                 for id in resources[kind]:
#                     doc = model.load(id=id, user=user, level=AccessType.ADMIN, exc=True)
# 
#                     # Don't do a subtree count if we weren't asked for progress
#                     if progress:
#                         subtotal = model.subtreeCount(doc)
#                         if subtotal != 1:
#                             total += subtotal - 1
#                             ctx.update(total=total)
# #                     model.remove(doc, progress=ctx)
#                     if progress:
#                         current += subtotal
#                         if ctx.progress['data']['current'] != current:
#                             ctx.update(current=current, message='Deleted ' + kind)
