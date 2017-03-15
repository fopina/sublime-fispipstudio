import sublime
import sublime_plugin
import os
import getpass
from io import BytesIO, StringIO
from fispip import PIP

# TODO: useless for now, as package requires ST3
IS_ST2 = int(sublime.version()) < 3000


class FisPipCommand(sublime_plugin.WindowCommand):
    def run(self, paths=None):
        c = self.find_config(paths)
        s = None
        try:
            s = Wrapper(c)
            self.run_wrapper(s, paths)
        except Exception as e:
            sublime.error_message(str(e))
        finally:
            if s:
                s.close()

    def get_path(self, paths=None, directory=False):
        path = None

        if paths is not None and len(paths) > 0:
            path = paths[0]
        else:
            view = None
            if hasattr(self, 'view'):
                view = self.view
            if view is None:
                if not hasattr(self, 'window'):
                    return
                view = self.window.active_view()
                if view is None:
                    path = self.window.extract_variables().get('file')

            if not path:
                path = view.file_name()

        if path and directory and not os.path.isdir(path):
            path = os.path.dirname(path)

        return path

    def find_config(self, paths=None):
        last_dir = None
        path = self.get_path(paths=paths, directory=True)
        while last_dir != path:
            file = os.path.join(path, 'fispip-config.json')
            if os.path.exists(file):
                return file
            last_dir = path
            path = os.path.abspath(os.path.join(last_dir, os.pardir))
        return None

    def is_visible(self, paths=None):
        if self.find_config(paths=paths) is None:
            return False
        return True


class FisPipFileCommand(FisPipCommand):
    EXTENSIONS = ['DAT', 'PROC', 'PSL', 'TBL', 'COL']

    def run_wrapper(self, wrapper, paths):
        filename = self.get_path(paths)
        self.run_wrapper_file(wrapper, filename, paths)

    def is_visible(self, paths=None):
        if not super(FisPipFileCommand, self).is_visible(paths):
            return False

        path = self.get_path(paths=paths)
        if os.path.isdir(path):
            return False

        return os.path.splitext(path)[1][1:].upper() in self.EXTENSIONS


class FisPipMissingConfigCommand(FisPipFileCommand):
    def run(self, paths=None):
        # does nothing
        pass

    def is_visible(self, paths=None):
        if super(FisPipMissingConfigCommand, self).is_visible(paths):
            return False
        return True

    def is_enabled(self, paths=None):
        return False

    def description(self, paths=None):
        if self.find_config(paths=paths) is None:
            return 'Missing configuration...'
        return 'Nothing to do...'


class FisPipSendCommand(FisPipFileCommand):
    def run_wrapper_file(self, wrapper, filename, paths):
        wrapper.send_element(filename)
        sublime.status_message('%s sent to host' % filename)


class FisPipGetCommand(FisPipCommand):
    def run(self, paths=None):
        self.window.show_input_panel(
            'Element name (with extension)',
            '', lambda x: self.run_get(paths, x),
            None, None
        )

    def run_get(self, paths, input):
        c = self.find_config(paths)
        s = Wrapper(c)
        try:
            filename = os.path.join(
                self.get_path(paths, directory=True),
                input
            )
            fobj = open(filename, 'wb')
            s.get_element_by_name(filename, file_obj=fobj)
            sublime.status_message('%s retrieved from host' % filename)
        except Exception as e:
            sublime.error_message(str(e))
        finally:
            s.close()


class FisPipRefreshCommand(FisPipFileCommand):
    def run_wrapper_file(self, wrapper, filename, paths):
        fobj = open(filename, 'wb')
        wrapper.get_element_by_name(filename, file_obj=fobj)
        sublime.status_message('%s refreshed' % filename)


class FisPipCompileAndLinkCommand(FisPipFileCommand):
    EXTENSIONS = ['PROC', 'PSL', 'TBL', 'COL']

    def run_wrapper_file(self, wrapper, filename, paths):
        try:
            wrapper.compile_and_link(filename)
            sublime.status_message('%s compiled' % filename)
        except Exception as e:
            if e.args[0] != 'ER_SV_INVLDRPC':
                raise e
            if sublime.ok_cancel_dialog(
                '"Compile and Link" requires MRPC081 which seems to be'
                'disabled in this host.\n'
                'Do you want to enable it and proceed with compilation?'
            ):
                wrapper.enable_mrpc081()
                wrapper.compile_and_link(filename)
                sublime.status_message('%s compiled' % filename)


class FisPipTestCompileCommand(FisPipFileCommand):
    EXTENSIONS = ['PROC', 'PSL']
    phantom_sets_by_buffer = {}

    def run_wrapper_file(self, wrapper, filename, paths):
        output = wrapper.test_compile_element(filename).replace('\r', '')

        if not hasattr(self, 'output_view'):
            # Try not to call get_output_panel until the regexes are assigned
            self.output_view = self.window.create_output_panel("fispipstudio")
        self.window.run_command("show_panel", {"panel": "output.fispipstudio"})
        self.output_view.run_command(
             'append',
             {'characters': output, 'force': True, 'scroll_to_end': True}
        )

        sublime.status_message(output.splitlines()[-2])
        self.add_phantoms(output)

    def add_phantoms(self, output):
        view = self.window.active_view()
        if not view:
            return

        lines = output.splitlines()
        errors = []
        for line_nr, line in enumerate(lines):
            if line[:6] in ['%PSL-W', '%PSL-E']:
                # At source code line: XX in subroutine...
                error_line = int(lines[line_nr+1][21:].split(' ')[0])
                errors.append((line[5], error_line, line))

        buffer_id = view.buffer_id()
        if buffer_id not in self.phantom_sets_by_buffer:
            phantom_set = sublime.PhantomSet(view, 'fispipstudio')
            self.phantom_sets_by_buffer[buffer_id] = phantom_set
        else:
            phantom_set = self.phantom_sets_by_buffer[buffer_id]

        stylesheet = '''
            <style>
                div.error {
                    padding: 0.4rem 0 0.4rem 0.7rem;
                    margin: 0.2rem 0;
                    border-radius: 2px;
                }

                div.error span.message {
                    padding-right: 0.7rem;
                }

                div.error a {
                    text-decoration: inherit;
                    padding: 0.35rem 0.7rem 0.45rem 0.8rem;
                    position: relative;
                    bottom: 0.05rem;
                    border-radius: 0 2px 2px 0;
                    font-weight: bold;
                }
                html.dark div.error a {
                    background-color: #00000018;
                }
                html.light div.error a {
                    background-color: #ffffff18;
                }
            </style>
        '''

        # TODO: phantoms not available in ST2...
        phantoms = []
        for type_, line, text in errors:
            pt = view.text_point(line - 1, 0)
            phantoms.append(
                sublime.Phantom(
                    sublime.Region(pt, view.line(pt).b),
                    '<body id=inline-error>%s'
                    '<div class="error%s">'
                    '<span class="message">%s</span>'
                    '<a href=hide>%s</a></div>'
                    '</body>' % (
                        stylesheet,
                        ' warning' if type_ == 'W' else '',
                        text,
                        chr(0x00D7)
                    ),
                    sublime.LAYOUT_BELOW,
                    on_navigate=self.on_phantom_navigate
                )
            )

        phantom_set.update(phantoms)

    def on_phantom_navigate(self, *args):
        view = self.window.active_view()
        if view:
            view.erase_phantoms("fispipstudio")
        self.phantom_sets_by_buffer = {}


class FisPipRunPslCommand(FisPipTestCompileCommand):
    EXTENSIONS = ['PROC', 'PSL']

    def run_wrapper_file(self, wrapper, filename, paths):
        try:
            output = wrapper.run_psl(filename)
        except Exception as e:
            if e.args[0] != 'ER_SV_INVLDRPC':
                raise e
            if not sublime.ok_cancel_dialog(
                '"Run PSL" requires a custom MRPC to expose PSLRUN^TBXDQSVR.\n'
                'Do you want to enable it and proceed with operation?'
            ):
                return
            wrapper.enable_mrpc99999()
            output = wrapper.run_psl(filename)
        output = output.replace('\r', '')

        if not hasattr(self, 'output_view'):
            # Try not to call get_output_panel until the regexes are assigned
            self.output_view = self.window.create_output_panel("fispipstudio")
        self.window.run_command("show_panel", {"panel": "output.fispipstudio"})
        self.output_view.run_command(
             'append',
             {'characters': output, 'force': True, 'scroll_to_end': True}
        )

        sublime.status_message(output.splitlines()[-2])
        self.add_phantoms(output)


class FisPipCreateConfigCommand(FisPipCommand):
    def run(self, paths=None):
        path = self.get_path(paths=paths, directory=True)
        file = os.path.join(path, 'fispip-config.json')
        # TODO: load_resource not available in ST2
        # use json.loads() with regex to remove comments..?
        template = sublime.load_resource(
            'Packages/FISPIP Studio/fispip-config.json.template'
        )
        open(file, 'w').write(template)
        self.window.run_command('open_file', {'file': file})

    def is_visible(self, paths=None):
        return not super(FisPipCreateConfigCommand, self).is_visible(paths)


class FisPipEditConfigCommand(FisPipCommand):
    def run(self, paths=None):
        file = self.find_config(paths=paths)
        if file:
            self.window.run_command('open_file', {'file': file})


class MRPC121(object):
    def __init__(self, connection=None, mrpc_id='121'):
        self._con = connection
        self._id = mrpc_id

    def _call(self, *args):
        return self._con.executeMRPC(self._id, *args, success_unpack=True)[0]

    def init_obj(self, obj_type, obj_id):
        r = self._call(
            'INITOBJ',
            '', '', '', obj_type, obj_id
        )
        if r[0] == '0':
            # some of the TBX routines split code and message
            # with | (pipe - such as Data)
            # others split with '\r' (such as Procedure)
            # and the default "Invalid Type" error is split with '\r\n'
            # why? Try to workaround it.....
            err = r[2:]
            if err[0] == '\n':
                err = err[1:]
            raise Exception(r[2:])
        return r.split('\r\n')[1:]

    def ret_obj(self, token):
        r = self._call(
            'RETOBJ',
            '', '', '', '', '', token
        )
        has_more = r[0] == '1'
        return has_more, r[1:]

    def init_code(self, code, compilation_token):
        return self._call(
            'INITCODE',
            code, compilation_token
        )

    def check_obj(self, local_file, token):
        r = self._call(
            'CHECKOBJ',
            '', '', local_file, '', '', token
        )
        if r[0] == '0':
            raise Exception(r[3:])

    def save_obj(self, local_file, token, username):
        r = self._call(
            'SAVEOBJ',
            '', '', local_file, '', '', token, username
        )
        if r[0] == '0':
            raise Exception(r[3:])

    def exec_comp(self, local_file, compilation_token):
        r = self._call(
            'EXECCOMP',
            '', compilation_token, local_file
        )
        return r


class MRPC081(object):
    """
    This procedure exists in PIP but it is not registered as an RPC
    """
    def __init__(self, connection=None, mrpc_id='81'):
        self._con = connection
        self._id = mrpc_id

    def _call(self, *args):
        return self._con.executeMRPC(self._id, *args, success_unpack=True)[0]

    def compile(self, table, elements):
        return self._call(table, elements)


class MRPC99999(object):
    """
    Custom RPC to expose PSLRUN^TBXDQSVR funcitonality
    """
    def __init__(self, connection=None, mrpc_id='99999'):
        self._con = connection
        self._id = mrpc_id

    def _call(self, *args):
        return self._con.executeMRPC(self._id, *args, success_unpack=True)[0]

    def execute(self, compilation_token):
        return self._call(compilation_token)


class Wrapper(PIP):
    OBJ_TYPES = {
        # incomplete mapping based on TBXDQSVR.m
        'DAT': 'Data',
        'PROC': 'Procedure',
        'PSL': 'Procedure',
        'TBL': 'Table',
        'COL': 'Column'
    }

    def __init__(self, conf_file):
        opts = sublime.decode_value(open(conf_file, 'r').read())
        super(Wrapper, self).__init__(opts['server'])
        self._rpc = MRPC121(self)
        self._rpc81 = MRPC081(self)
        self._rpc99999 = MRPC99999(self)
        self._user = opts['user']
        self.connect(
            opts['host'], opts['port'],
            opts['user'], opts['password']
        )

    def guess_type(self, filename):
        filename = os.path.basename(filename)
        name, ext = os.path.splitext(filename)
        if ext:
            ext = self.OBJ_TYPES.get(ext[1:].upper(), '')
        return ext, name

    def get_element_by_name(self, filename, file_obj=None):
        obj_type, obj_id = self.guess_type(filename)
        return self.get_element(obj_type, obj_id, file_obj)

    def get_element(self, obj_type, obj_id, file_obj=None):
        if file_obj is None:
            file_obj = BytesIO()
        token, name = self._rpc.init_obj(obj_type, obj_id)

        has_more = True
        while has_more:
            has_more, text = self._rpc.ret_obj(token)
            if IS_ST2:
                file_obj.write(text)
            else:
                file_obj.write(text.encode())
        return file_obj

    def send_element(self, filename, file_obj=None, close_file=True):
        if file_obj is None:
            file_obj = open(filename, 'rb')
            close_file = True

        token = self._send_code(file_obj, close_file)
        local_file = os.path.basename(filename)
        self._rpc.check_obj(local_file, token)
        self._rpc.save_obj(local_file, token, getpass.getuser())

    def test_compile_element(self, filename, file_obj=None, close_file=True):
        if file_obj is None:
            file_obj = open(filename, 'rb')
            close_file = True

        token = self._send_code(file_obj, close_file)
        local_file = os.path.basename(filename)
        return self._rpc.exec_comp(local_file, token)

    def compile_and_link(self, filename):
        ext, name = self.guess_type(filename)
        if ext == 'Procedure':
            table = 'DBTBL25'
        elif ext in ['Table', 'Column']:
            table = 'DBTBL1'
        else:
            raise Exception('Cannot compile', ext, name)
        return self._rpc81.compile(table, name)

    def run_psl(self, filename, file_obj=None, close_file=True):
        if file_obj is None:
            file_obj = open(filename, 'rb')
            close_file = True

        token = self._send_code(file_obj, close_file)
        local_file = os.path.basename(filename)
        return self._rpc99999.execute(token)

    def enable_mrpc081(self):
        if self.executeSQL(
            'SELECT RPCID FROM SCATBL5 WHERE RPCID=81'
        )[0][0] == '81':
            return

        ucls = self.executeSQL(
            'SELECT %UCLS FROM SCAU WHERE UID=?',
            self._user
        )[0][0]
        self.executeSQL(
            "INSERT INTO SCATBL5 "
            "(%SN,RPCID,MRPC,DESC,LOGFLG,PARAM01,PARAM02) VALUES"
            "('PBS','81','$$^MRPC081','Compile DQ Runtime Routines',1,1,1)"
        )
        # TODO: pyfispip not supporting markers on INSERT
        # update this once it does
        self.executeSQL(
            "INSERT INTO SCATBL5A (RPCID,UCLS,AUTH,LOGFLG) "
            "VALUES('81','%s',0,0)" % ucls
        )

    def enable_mrpc99999(self):
        if self.executeSQL(
            'SELECT RPCID FROM SCATBL5 WHERE RPCID=99999'
        )[0][0] == '99999':
            return

        self.enable_mrpc081()
        template = sublime.load_resource(
            'Packages/FISPIP Studio/MRPC99999.PROC'
        )
        fobj = StringIO(template)
        self.send_element('MRPC99999.PROC', file_obj=fobj)
        self.compile_and_link('MRPC99999.PROC')

        ucls = self.executeSQL(
            'SELECT %UCLS FROM SCAU WHERE UID=?',
            self._user
        )[0][0]
        self.executeSQL(
            "INSERT INTO SCATBL5 "
            "(%SN,RPCID,MRPC,DESC,LOGFLG,PARAM01) VALUES"
            "('PBS','99999','$$^MRPC99999','Run PSL Remotely',1,1)"
        )
        # TODO: pyfispip not supporting markers on INSERT
        # update this once it does
        self.executeSQL(
            "INSERT INTO SCATBL5A (RPCID,UCLS,AUTH,LOGFLG) "
            "VALUES('99999','%s',0,0)" % ucls
        )

    def _send_code(self, file_obj, close_file=True):
        token = ''

        try:
            while True:
                data = file_obj.read(1024)
                if not data:
                    break
                if not IS_ST2 and isinstance(data, bytes):
                    enc_data = '|'.join(str(x) for x in data) + '|'
                else:
                    enc_data = '|'.join(str(ord(x)) for x in data) + '|'
                token = self._rpc.init_code(enc_data, token)
            # one last call to make sure code is saved even if it doesn't end
            # in a NEWLINE (condition based on INITCOD1^TBXDQSVR.m)
            # this is also useful in case we're trying to send an empty file
            # in which case, the previous while loop did not initialize
            # the token (required for check_obj and save_obj)
            token = self._rpc.init_code('', token)
        finally:
            if close_file:
                file_obj.close()

        return token
