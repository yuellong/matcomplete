# Copyright (C) 2011, 2012  Stephen Sugden <me@stephensugden.com>
#                           Strahinja Val Markovic <val@markovic.io>
#                           Stanislav Golovanov <stgolovanov@gmail.com>
#
# This file is part of YouCompleteMe.
#
# YouCompleteMe is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# YouCompleteMe is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with YouCompleteMe.  If not, see <http://www.gnu.org/licenses/>.

import re
from ycmd import responses
from ycmd.completers.completer import Completer
from ycmd.completers.matlab.oct_daemon import PyOct
import os


class OctCompleter( Completer ):
    """
    A Completer that embeds an matlab interpreter
    """

    def __init__( self, user_options ):
        super( OctCompleter, self ).__init__( user_options )

        self.path = '/dev/shm/matlab_completer_temp_buffer.m'
        self.matchpairs = ((re.compile(r"\bif\b"),
                            re.compile(r"(\bendif|^end|[^:\s]\s*end)\b")),
                (re.compile(r"\b(for|parfor)\b"), re.compile(
                    r"(\bendfor|^end|[^:\s]\s*end)\b")),
                (re.compile(r"\bwhile\b"), re.compile(
                    r"(\bendwhile|^end|[^:\s]\s*end)\b")),
                (re.compile(r"\bswitch\b"), re.compile(
                    r"(\bendswitch|^end|[^:\s]\s*end)\b")),
                (re.compile(r"\bunwind\_protect\b"),
                    re.compile(r"\bend\_unwind\_protect\b")))
        self.comment_pat = re.compile(r"[#%].*")
        self.fields_pat = re.compile(r'(?:\w+\.)+')
        self.field_elem_pat = re.compile(r'\w+(?=\.)')
        self.daemon = PyOct()

    def SupportedFiletypes( self ):
        """ Just matlab """
        return set([ 'matlab' ])

    def ComputeCandidatesInner( self, request_data ):
        self._UpdateCurrentBuffer(request_data, discard_current_line=True)
        current_line = request_data[ 'line_value' ]
        start_column = request_data[ 'start_column' ]
        line = current_line[ :start_column ]
        if len(line) >= 2 and line[start_column - 2] == '.':
            fields = self.fields_pat.findall(line[:start_column])
            if len(fields):
                namelist = self.field_elem_pat.findall(fields[-1])
                return [ responses.BuildCompletionData(
                    str( completion[ 'word' ] ),
                    str( completion[ 'menu' ] ) )
                    for completion in self.daemon.get_fields(namelist) ]
        else:
            return [ responses.BuildCompletionData(
                str( completion[ 'word' ] ),
                str( completion[ 'menu' ] ), )
                for completion in self.daemon.get_candidates() ]

    def GetSubcommandsMap( self ):
        return {
            'GoTo': (lambda self, request_data, args:
                               self._GoToDefinition( request_data ) ),
            'GetDoc'        : (lambda self, request_data, args:
                               self._GetDoc( request_data ) )
        }

    def Shutdown(self):
        if os.path.isfile(self.path):
            os.remove(self.path)

    def ShouldUseNowInner( self, request_data ):
        if not self.prepared_triggers:
            return self.QueryLengthAboveMinThreshold( request_data )
        current_line = request_data[ 'line_value' ]
        start_codepoint = request_data[ 'start_codepoint' ] - 1
        column_codepoint = request_data[ 'column_codepoint' ] - 1
        filetype = self._CurrentFiletype( request_data[ 'filetypes' ] )

        return True if self.prepared_triggers.MatchesForFiletype(
              current_line, start_codepoint, column_codepoint, filetype ) \
            else self.QueryLengthAboveMinThreshold( request_data )

    def OnFileReadyToParse( self, request_data ):
        self._UpdateCurrentBuffer( request_data )

    def _FixMissingPairs( self, raw ):
        stack = []
        modified = []
        for line in raw:
            lc = self.comment_pat.sub('', line)
            modified.append(line)
            for idx, (beg_pat, end_pat) in enumerate(self.matchpairs):
                # I don't think any saned people would use multiple statement
                # pairs in a single line :)
                if beg_pat.search(lc):
                    stack.append(idx)
                if end_pat.search(lc):
                    if len(stack) and stack[-1] == idx:
                        del stack[-1]
        for tr_i in range(len(stack)):
            modified.append('end')

        return modified

    def _UpdateCurrentBuffer( self, request_data,
                            discard_current_line=False ):
        line = request_data[ 'line_num' ]
        filename = request_data[ 'filepath' ]
        contents = request_data[ 'file_data' ][filename][ 'contents' ]
        current_buffer = contents.splitlines()
        with open(self.path, 'w') as f:
            if discard_current_line:
                contents = '\n'.join(current_buffer[:line]
                    + current_buffer[line + 1:])
            f.write(contents)

        if not self.daemon.update(self.path):
            if discard_current_line:
                modified = self._FixMissingPairs(current_buffer)
            else:
                modified = self._FixMissingPairs(current_buffer[:line]
                        + current_buffer[line + 1:])
            with open(self.path, 'w') as f:
                contents = '\n'.join(modified)
                f.write(contents)

            self.daemon.update(self.path)


    def _GoToDefinition( self, request_data ):
        self._UpdateCurrentBuffer(request_data)
        col = request_data[ 'column_num' ] - 1
        keyword = ''
        for it in re.finditer( r'\b[\w\.]+\b', request_data[ 'line_value' ] ):
            if it.start() <= col and it.end() > col:
                keyword = it.group(0)
                break
        (filepath, line, column) = self.daemon.find(keyword)

        if line == -1 and column == -1:
            raise RuntimeError( 'Definition for "%s" not found' % keyword )
        if line == -2: # reserved for quick return
            return
        if filepath == '' and line != -1 and column != -1:
            filepath = request_data[ 'filepath' ]
        if filepath.endswith('.oct') or filepath.endswith('.mex'):
            raise RuntimeError( '"%s" is a shared module.' % keyword )
        elif os.path.isfile(filepath):
            line = 1 if line <= 0 else line
            column = 0 if column < 0 else column
            return responses.BuildGoToResponse( filepath, line, column)
        else:
            raise RuntimeError( 'Source for "%s" is unavailable.' % keyword )

    def _GetDoc( self, request_data ):
        self._UpdateCurrentBuffer(request_data)
        col = request_data[ 'column_num' ] - 1
        keyword = ''
        for it in re.finditer( r'\b[\w\.]+\b', request_data[ 'line_value' ] ):
            if it.start() <= col and it.end() > col:
                keyword = it.group(0)
                break
        doc = self.daemon.query(keyword)
        if doc:
            return responses.BuildDetailedInfoResponse(doc)
        else:
            raise ValueError('No documentation available')
