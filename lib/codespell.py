'''
Module for spell-checking programming language source code.  The trick
is that it knows how to split identifiers up into words: e.g. if the
token getRemaningObjects occurs in source code, it is split into "get",
"Remaning", "Objects", and those words are piped to ispell, which easily
detects the spelling error.  Handles various common ways of munging
words together: identifiers like DoSomethng, get_remaning_objects,
SOME_CONSTENT, and HTTPRepsonse are all handled correctly.

Requires Python 2.3 or greater.
'''

import sys, os
import re
from sets import Set

assert sys.hexversion >= 0x02030000, "requires Python 2.3 or greater"


def warn(msg):
    sys.stderr.write("warning: %s\n" % msg)

def error(msg):
    sys.stderr.write("error: %s\n" % msg)

class SpellChecker:
    '''
    A wrapper for ispell.  Opens two pipes to ispell: one for writing
    (sending) words to ispell, and the other for reading reports
    of misspelled words back from it.
    '''

    def __init__(self):
        cmd = ["ispell", "-a"]
        #cmd = ["strace", "-o", "ispell-pipe.log", "ispell", "-a"]
        (self.ispell_in, self.ispell_out) = os.popen2(cmd, "t", 1)
        firstline = self.ispell_out.readline()
        assert firstline.startswith("@(#)"), \
               "expected \"@(#)\" line from ispell (got %r)" % firstline

        # Put ispell in terse mode (no output for correctly-spelled
        # words).
        self.ispell_in.write("!\n")

        # Total number of unique spelling errors seen.
        self.total_errors = 0

    def close():
        in_status = self.ispell_in.close()
        out_status = self.ispell_out.close()
        if in_status != out_status:
            warn("huh? ispell_in status was %r, but ispell_out status was %r"
                 % (in_status, out_status))
        elif in_status is not None:
            warn("ispell failed with exit status %r" % in_status)

    def send(self, word):
        '''Send a word to ispell to be checked.'''
        #print "sending %r to ispell" % word
        self.ispell_in.write("^" + word + "\n")

    def done_sending(self):
        self.ispell_in.close()

    def check(self):
        '''
        Read any output available from ispell, ie. reports of misspelled
        words sent since initialization.  Return a list of tuples
        (bad_word, guesses) where 'guesses' is a list (possibly empty)
        of suggested replacements for 'guesses'.
        '''
        report = []                     # list of (bad_word, suggestions)
        while True:
            #(ready, _, _) = select.select([self.ispell_out], [], [])
            #if not ready:               # nothing to read
            #    break
            #assert ready[0] is self.ispell_out

            line = self.ispell_out.readline()
            if not line:
                break

            code = line[0]
            extra = line[1:-1]

            if code in "&?":
                # ispell has near-misses or guesses, formatted like this:
                #   "& orig count offset: miss, miss, ..., guess, ..."
                #   "? orig 0 offset: guess, ..."
                # I don't care about the distinction between near-misses
                # and guesses.
                (orig, count, offset, extra) = extra.split(None, 3)
                count = int(count)
                guesses = extra.split(", ")
                report.append((orig, guesses))
            elif code == "#":
                # ispell has no clue
                orig = extra.split()[0]
                report.append((orig, []))

        self.total_errors += len(report)
        return report


class CodeChecker(object):
    '''
    Object that reads a source code file, splits it into tokens,
    splits the tokens into words, and spell-checks each word.
    '''

    __slots__ = [
        # Name of the file currently being read.
        'filename',

        # The file currently being read.
        'file',

        # Current line number in 'file'.
        'line_num',

        # Map word to list of line numbers where that word occurs, and
        # coincidentally allows us to prevent checking the same word
        # twice.
        'locations',

        # SpellChecker object -- a pair of pipes to send words to ispell
        # and read errors back.
        'ispell',

        # The programming language of the current file (used to determine
        # excluded words).  This can be derived either from the filename
        # or from the first line of a script.
        'language',

        # User-specified excluded strings; these are added to
        # BASE_EXCLUDE and one of the LANG_EXCLUDE before creating
        # 'exclude' and 'exclude_re'.
        'custom_exclude',

        # Set of strings and words that are excluded from
        # spell-checking.


        'exclude',

        # Regex used to strip excluded strings from input.
        'exclude_re',

        # If true, report each misspelling only once (at its first
        # occurrence).
        'unique',

        ]

    # Exclusions -- tokens that should not be split into words and
    # should not be spell-checked.  These are removed from input text
    # before it is split into words, and then again from the list of
    # split words.  (We have to remove them before splitting so people
    # can exclude local mixed-case trade names like "WhizzBANGulator" --
    # you don't want codespell to split that into words and then have to
    # exclude the individual words.  But we also have to remove them
    # after splitting because of method names like "parse_args", where
    # "args" is an excluded word.)

    # Exclusions that apply to several programming languages.  There is
    # a slight Unix bias here.  ;-)
    BASE_EXCLUDE = ["usr",               # as in "#!/usr/bin/python"
                    "argv",              # Python; C and Java by convention
                    "strerror",          # Python, C
                    "errno",
                    "stdout",
                    "stderr",
                    "readline",
                    
                    # Data type names in C, C++, Python, Java, ...
                    "int",
                    "char",
                    "bool",
                   ]

    # Per-language exclusions.
    LANG_EXCLUDE = {
        "python": ["def",
                   "elif",
                   "sys",
                   "os",
                   "startswith",
                   "endswith",
                   "optparse",
                   "prog",
                   "args",
                   "metavar",
                  ],

        "c":      [
                  ],

        "java":   [
                  ],

        "perl":   [
                  ],
        }

    EXTENSION_LANG = {".py": "python",
                      ".c": "c",
                      ".h": "c",
                      ".cpp": "c",
                      ".hpp": "c",
                      ".java": "java"}


    def __init__(self, filename=None, file=None):
        self.filename = filename
        if file is None and filename is not None:
            self.file = open(filename, "rt")
        else:
            self.file = file

        self.line_num = 0
        self.locations = {}
        self.ispell = SpellChecker()

        self.language = None
        self.custom_exclude = []
        self.exclude = {}
        self.exclude_re = None
        self.unique = False

        # Try to determine the language from the filename, and from
        # that get the list of exclusions.
        if filename:
            ext = os.path.splitext(filename)[1]
            lang = self.EXTENSION_LANG.get(ext)
            if lang:
                self.set_language(lang)

    def exclude_string(self, string):
        '''
        Exclude 'string' from spell-checking.
        '''
        self.custom_exclude.append(string)

    def set_language(self, lang):
        '''
        Set the language for the current file, and set the list
        of excluded words based on the language.  Raises ValueError
        if 'lang' is unknown.
        '''
        if lang not in self.LANG_EXCLUDE:
            raise ValueError("unknown language: %r" % lang)
        self.language = lang

        # Determine the final list of excluded strings (and, more
        # importantly, the regex that will be used to strip them from
        # input text).
        exclusions = (self.BASE_EXCLUDE +
                      self.LANG_EXCLUDE[self.language] +
                      self.custom_exclude)
        self.exclude_re = re.compile(r'\b(' +
                                     "|".join(exclusions) +
                                     r')\b')
        print "language is %r" % self.language
        print "excluded strings: %r" % exclusions
        print "exclusion regex: %s" % self.exclude_re.pattern
        self.exclude = Set(exclusions)

    def guess_language(self, first_line):
        '''
        Attempt to guess the programming language of the current file
        by examining the first line of source code.  Mainly useful
        for Unix scripts with a #! line.
        '''
        if not first_line.startswith("#!"):
            return
        if "python" in first_line:
            self.set_language("python")
        elif "perl" in first_line:
            self.set_language("perl")

    def set_unique(self, unique):
        self.unique = unique

    # A word is either:
    #   1) a string of letters, optionally capitalized; or
    #   2) a string of uppercase letters not immediately followed
    #      by a lowercase letter
    # Case 1 handles almost everything, eg. "getNext", "get_next",
    # "GetNext", "HTTP_NOT_FOUND", "HttpResponse", etc.  Case 2 is
    # needed for uppercase acronyms in mixed-case identifiers,
    # eg. "HTTPResponse", "getHTTPResponse".
    _word_re = re.compile(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])')

    def split(self, line):
        '''
        Given a line (or larger chunk) of source code, splits it
        into words.  Eg. the string
          "match = pat.search(current_line, 0, pos)"
        is split into
          ["match", "pat", "search", "current", "line", "pos"]
        '''
        if self.exclude_re:
            line = self.exclude_re.sub('', line)
        return [word
                for word in self._word_re.findall(line)
                if word not in self.exclude]

    def _send_words(self):
        for line in self.file:
            # If this is the first line of the file, and we don't yet
            # know the programming language, try to guess it from the
            # content of the line (which might be something like
            # "#!/usr/bin/python" or "#!/usr/bin/perl")
            if self.line_num == 0 and self.language is None:
                self.guess_language(line)

            self.line_num += 1
            for word in self.split(line):
                if word in self.locations:
                    self.locations[word].append(self.line_num)
                else:
                    self.locations[word] = [self.line_num]
                    #print "%d: %s" % (self.line_num, word)
                    self.ispell.send(word)

        self.ispell.done_sending()

    def _check(self):
        '''
        Report spelling errors found in the current file to stderr.
        Return true if there were any spelling errors.
        '''
        messages = []
        for (bad_word, guesses) in self.ispell.check():
            if guesses:
                message = "%s: %s ?" % (bad_word, ", ".join(guesses))
            else:
                message = "%s ?" % bad_word

            if self.unique:
                messages.append((self.locations[bad_word][0], message))
            else:
                for line_num in self.locations[bad_word]:
                    messages.append((line_num, message))

        messages.sort()
        for (line_num, message) in messages:
            sys.stderr.write("spelling: %s:%d: %s\n"
                             % (self.filename, line_num, message))
        return bool(messages)

    def check_file(self):
        '''
        Spell-check the current file, reporting errors to stderr.
        Return true if there were any spelling errors.
        '''
        print "spell-checking %r" % self.filename
        self._send_words()
        return self._check()


if __name__ == "__main__":
    import sys
    sys.exit(CodeChecker(sys.argv[1]).check_file() and 1 or 0)
