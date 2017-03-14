import sublime
import sublime_plugin
import os
import getpass
from io import BytesIO
from fispip import PIP

# TODO: useless for now, as package requires ST3
IS_ST2 = int(sublime.version()) < 3000


class FisPipCommand(sublime_plugin.WindowCommand):
    def run(self, paths=None):
        c = self.find_config(paths)
        s = Wrapper(c)
        try:
            self.run_wrapper(s, paths)
            sublime.status_message('Done')
        except Exception as e:
            sublime.error_message(str(e))
        finally:
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


class FisPipSendCommand(FisPipCommand):
    def run_wrapper(self, wrapper, paths):
        filename = self.get_path(paths)
        wrapper.send_element(filename)


class FisPipRefreshCommand(FisPipCommand):
    def run_wrapper(self, wrapper, paths):
        filename = self.get_path(paths)
        fobj = open(filename, 'wb')
        wrapper.get_element_by_name(filename, file_obj=fobj)


class FisPipTestCompileCommand(FisPipCommand):
    phantom_sets_by_buffer = {}

    def run_wrapper(self, wrapper, paths):
        filename = self.get_path(paths)
        output = wrapper.test_compile_element(filename).replace('\r', '')

        if not hasattr(self, 'output_view'):
            # Try not to call get_output_panel until the regexes are assigned
            self.output_view = self.window.create_output_panel("fispipstudio")
        self.window.run_command("show_panel", {"panel": "output.fispipstudio"})
        self.output_view.run_command(
             'append',
             {'characters': output, 'force': True, 'scroll_to_end': True}
        )

        view = sublime.active_window().active_view()
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
        view = sublime.active_window().active_view()
        if view:
            view.erase_phantoms("fispipstudio")
        self.phantom_sets_by_buffer = {}


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
        sublime.active_window().run_command('open_file', {'file': file})

    def is_visible(self, paths=None):
        return not super(FisPipCreateConfigCommand, self).is_visible(paths)


class FisPipEditConfigCommand(FisPipCommand):
    def run(self, paths=None):
        file = self.find_config(paths=paths)
        if file:
            sublime.active_window().run_command('open_file', {'file': file})


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


class Wrapper(PIP):
    OBJ_TYPES = {
        # incomplete mapping based on TBXDQSVR.m
        'DAT': 'Data',
        'PROC': 'Procedure',
        'TBL': 'Table',
        'COL': 'Column'
    }

    def __init__(self, conf_file):
        opts = sublime.decode_value(open(conf_file, 'r').read())
        super(Wrapper, self).__init__(opts['server'])
        self._rpc = MRPC121(self)
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
