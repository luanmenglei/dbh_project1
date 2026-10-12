CXX ?= c++
PG_CONFIG ?= pg_config
CPPFLAGS += -I$(shell $(PG_CONFIG) --includedir)
CXXFLAGS ?= -O3 -std=c++17 -Wall -Wextra -Wpedantic
LDFLAGS += -L$(shell $(PG_CONFIG) --libdir) -Wl,-rpath,$(shell $(PG_CONFIG) --libdir)
LDLIBS += -lpq -pthread
TARGET ?= build/filmbench

all: $(TARGET)

$(TARGET): src/filmbench.cpp
	mkdir -p build
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $< $(LDFLAGS) $(LDLIBS) -o $@

test: all
	python3 tests/test_project.py

.PHONY: all test
