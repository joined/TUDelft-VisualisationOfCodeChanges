package nl.tud.mcextractor;

import com.github.javaparser.JavaParser;
import com.github.javaparser.ParserConfiguration;
import com.github.javaparser.Range;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.github.javaparser.ast.expr.MethodCallExpr;
import com.github.javaparser.resolution.declarations.ResolvedMethodDeclaration;
import com.github.javaparser.symbolsolver.JavaSymbolSolver;
import com.github.javaparser.symbolsolver.javaparsermodel.declarations.JavaParserMethodDeclaration;
import com.github.javaparser.symbolsolver.resolution.typesolvers.CombinedTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.JavaParserTypeSolver;
import com.github.javaparser.symbolsolver.resolution.typesolvers.ReflectionTypeSolver;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;

public class MethodCallExtractor {
    private Path repository;
    private ArrayList<Path> modified_classes;
    private ArrayList<Path> possible_caller_classes;
    private Set<Path> package_roots;
    private ArrayList<String> possible_caller_method_names;

    public MethodCallExtractor(Path repository, ArrayList<Path> modified_classes) {
        this.repository = repository;
        this.modified_classes = modified_classes;
        this.possible_caller_method_names = new ArrayList<>();
    }

    public boolean inModifiedClasses(Path path) throws IOException {
        for (Path modified_class : modified_classes) {
            if (Files.isSameFile(path, modified_class)) return true;
        }
        return false;
    }

    public ArrayList<String> processClass(Path file, boolean callerCheck) throws IOException {
        ArrayList<String> methodCallsFormatted = new ArrayList<>();
        CompilationUnit cu = JavaParser.parse(file);

        List<MethodCallExpr> methodCalls = cu.findAll(MethodCallExpr.class);
        for (MethodCallExpr mc : methodCalls) {
            try {
                if (!possible_caller_method_names.contains(mc.getName().toString())) continue;
                ResolvedMethodDeclaration resolvedDeclaration = mc.resolve();
                // A declaration can be also of an internal java method, we're not interested in those
                if (resolvedDeclaration instanceof JavaParserMethodDeclaration) {
                    JavaParserMethodDeclaration correspondingDeclaration = (JavaParserMethodDeclaration) resolvedDeclaration;
                    Node wrappedNode = correspondingDeclaration.getWrappedNode();
                    Node temp = wrappedNode.findRootNode();

                    // Path of the method declaration in the target class
                    Path absolutePath = ((CompilationUnit) temp).getStorage().get().getPath();
                    Path relativePath = repository.toAbsolutePath().relativize(absolutePath);
                    if (callerCheck && !inModifiedClasses(absolutePath)) continue;

                    StringBuilder output = new StringBuilder();
                    // Path of the source java class (containing the method call)
                    output.append(repository.toAbsolutePath().relativize(file.toAbsolutePath()));
                    output.append(';');

                    // Token range of the method call
                    Range sourceRange = mc.getRange().get();
                    output.append(sourceRange.begin.line);
                    output.append(';');
                    output.append(sourceRange.begin.column);
                    output.append(';');
                    output.append(sourceRange.end.line);
                    output.append(';');
                    output.append(sourceRange.end.column);
                    output.append(';');

                    // Method call itself
                    output.append(mc.toString().split("[\r\n]+")[0].replace(";", "&%&"));
                    output.append(';');

                    // Signature of the invoked method
                    output.append(resolvedDeclaration.getSignature());
                    output.append(';');

                    // Qualified signature of the invoked method
                    output.append(resolvedDeclaration.getQualifiedSignature());
                    output.append(';');

                    output.append(relativePath);
                    output.append(';');

                    // Token range of the called method's declaration in target class
                    MethodDeclaration wd = (MethodDeclaration) wrappedNode;
                    Range targetRange = wd.getRange().get();
                    output.append(targetRange.begin.line);
                    output.append(';');
                    output.append(targetRange.begin.column);
                    output.append(';');
                    output.append(targetRange.end.line);
                    output.append(';');
                    output.append(targetRange.end.column);

                    methodCallsFormatted.add(output.toString());
                }
            } catch (Exception e) {
                // Unfortunately JavaParser can fail when it cannot resolve a method but it is not possible to be
                // more specific with the error handling because sometimes we just get a generic RuntimeException
                ;
            }
        }

        return methodCallsFormatted;
    }

    public ArrayList<Path> extractPossibleCallerClasses() throws IOException {
        // To extract the files that (potentially) contain a method call to one of the methods declared in
        // modified_classes, we perform the following steps.
        // The idea is that if a file in the codebase contains a method call do one of the methods declared in
        // modified_classes then it needs necessarily to contain the method name followed by "(".
        // Therefore, we extract all the names of the methods declared within modified_classes, add them to a set
        // (for deduplication) with the "(" suffix (escaped because RegEx).
        Set<String> declaredMethodNames = new HashSet<>();
        for (Path modified_class : modified_classes) {
            CompilationUnit cu = JavaParser.parse(modified_class);

            List<MethodDeclaration> methodDeclarations = cu.findAll(MethodDeclaration.class);
            for (MethodDeclaration md : methodDeclarations) {
                String methodName = md.getName().asString();
                this.possible_caller_method_names.add(methodName);
                declaredMethodNames.add(methodName + "\\(");
            }
        }

        // Then we perform a search, using again the RipGrep tool, for all the Java files in the codebase containing
        // at at least one of the names of the methods declared in modified_classes.
        String searchString = String.join("|", declaredMethodNames);
        String[] searchCmd = {"rg", "-l", "-g", "*.java", searchString, "."};

        Process rg = Runtime.getRuntime().exec(searchCmd, null, repository.toFile());

        ArrayList<Path> possible_caller_classes = new ArrayList<>();
        new BufferedReader(new InputStreamReader(rg.getInputStream())).lines()
                .forEach(relevantFile -> {
                    Path relevantFilePath = repository.resolve(relevantFile);
                    try {
                        // Add the class possibly containing a caller only if it's not already in the modified classes
                        if (!inModifiedClasses(relevantFilePath)) possible_caller_classes.add(relevantFilePath);
                    }
                    catch(IOException e) {
                        throw new UncheckedIOException(e);
                    }
                });

        return possible_caller_classes;
    }

    public Set<Path> extractPackageRoots() throws IOException {
        // To extract all the possible package roots, we perform a search with the tool RipGrep in all the Java
        // files in the repository for the package declaration at the top of the file.
        // Basing on the package identifier and the path of the file, we go back to the corresponding package root.
        String[] searchCmd = {"rg", "--vimgrep", "-g", "*.java", "^package \\w(.*?)\\w;$", "."};
        Process rg = Runtime.getRuntime().exec(searchCmd, null, repository.toFile());
        // Multiple Java classes have the same package root, so we use a set to deduplicate.
        Set<Path> package_roots = new HashSet<>();
        new BufferedReader(new InputStreamReader(rg.getInputStream())).lines()
                .forEach(line -> {
                    String[] splitLine =  line.split(":");
                    // Get the path to the class, relative to the repository
                    String java_class_string = splitLine[0];
                    // Package identifier, e.g. org.example.myprogram
                    String packageId = splitLine[3].substring(8, splitLine[3].length() - 1);
                    int numberPackageParts = packageId.split("\\.").length;
                    String[] pathParts = java_class_string.split("/");
                    String packageRootPath = String.join("/", Arrays.copyOfRange(pathParts, 0, pathParts.length - numberPackageParts - 1));
                    Path packageRoot = repository.resolve(packageRootPath);
                    package_roots.add(packageRoot);
                });
        return package_roots;
    }

    public ArrayList<String> run() throws IOException {
        // The modified_classes list is the list of files from which we want to extract callers and callees.
        // To extract the callees we just need to resolve all the method calls within these files.
        // To extract the callers we need to resolve all the method calls in the files that contain a method call
        // to a method declared in one of the modified_classes, and to find out which these files are we use the
        // method filesWithCallers.
        // We then create a set containing all the files from which we want to extract the method calls, which
        // will give us both the caller and callee relationships that we're looking for.
        // We use a set to avoid duplication.
        System.err.println("Extracting possible caller classes");
        possible_caller_classes = extractPossibleCallerClasses();

        // In order for the JavaSymbolSolver type solver to work, it needs as input the package root of the packages
        // containing the classes that we're trying to analyze. We perform an extraction step to extract all the
        // possible Java package roots in the repository.
        System.err.println("Extracting package roots");
        package_roots = extractPackageRoots();

        // Create a new type solver and add all the package roots found
        CombinedTypeSolver combinedTypeSolver = new CombinedTypeSolver();
        combinedTypeSolver.add(new ReflectionTypeSolver());
        ParserConfiguration pc = new ParserConfiguration().setAttributeComments(false);
        for (Path package_root : package_roots) combinedTypeSolver.add(new JavaParserTypeSolver(package_root, pc));
        JavaSymbolSolver symbolSolver = new JavaSymbolSolver(combinedTypeSolver);
        JavaParser.getStaticConfiguration().setSymbolResolver(symbolSolver);

        ArrayList<String> calleeMethodCallsFormatted = new ArrayList<>();
        ArrayList<String> callerMethodCallsFormatted = new ArrayList<>();
        // Perform the method call extraction on each of the files to analyze
        System.err.format("Extracting callees from %d classes\n", modified_classes.size());
        int lastPercentage = -1;
        for (int i = 0; i < modified_classes.size(); i += 1) {
            int rounded_percentage = (int) (((double) i / modified_classes.size()) * 100.0);
            if (rounded_percentage != lastPercentage) {
                System.err.format("Progress: %d%%\n", rounded_percentage);
                lastPercentage = rounded_percentage;
            }
            Path modified_class = modified_classes.get(i);
            calleeMethodCallsFormatted.addAll(processClass(modified_class, true));
        }
        System.err.format("Found %d callees\n", calleeMethodCallsFormatted.size());
        System.err.format("Extracting callers from %d classes\n", possible_caller_classes.size());
        int lastPercentage2 = -1;
        for (int i = 0; i < possible_caller_classes.size(); i += 1) {
            int rounded_percentage = (int) (((double) i / possible_caller_classes.size()) * 100.0);
            if (rounded_percentage != lastPercentage) {
                System.err.format("Progress: %d%%\n", rounded_percentage);
                lastPercentage = rounded_percentage;
            }
            Path possible_caller_class = possible_caller_classes.get(i);
            callerMethodCallsFormatted.addAll(processClass(possible_caller_class, true));
        }
        System.err.format("Found %d callers\n", callerMethodCallsFormatted.size());

        ArrayList<String> methodCallsFormatted = new ArrayList<>();
        methodCallsFormatted.addAll(calleeMethodCallsFormatted);
        methodCallsFormatted.addAll(callerMethodCallsFormatted);

        return methodCallsFormatted;
    }
}
